"""End-to-end orchestration tests: each runs real agents, policies and test suites."""

import asyncio
import json

from agentflow import audit
from agentflow.engine import Orchestrator, RunState
from agentflow.models import AgentError, NodeStatus


def test_greenfield_delivers_a_tested_released_service(driver):
    run = driver.start("greenfield")
    assert driver.drive(run) == "succeeded"
    m = driver.metrics(run)
    assert m["nodes_failed"] == 0 and m["approvals"] == 1  # only the release needs a human
    ws = run / "workspace"
    assert (ws / "VERSION").read_text().strip() == "1.0.0"
    assert {"openapi.json", "docs", "CHANGELOG.md", "README.md"} <= {p.name for p in ws.iterdir()}
    report = RunState(run).store.get("test_report")
    assert report["failed"] == 0 and report["coverage"] >= 90
    assert audit.verify(run / "audit.jsonl")[0]


def test_brownfield_rolls_back_a_defective_change_and_retries(driver):
    run = driver.start("brownfield")
    assert driver.drive(run) == "succeeded"
    m = driver.metrics(run)
    assert (m["rollbacks"], m["retries"], m["approvals"]) == (1, 1, 2)
    assert m["mttr_s"] is not None
    expiry = driver.node(run, "impl.expiry")
    assert expiry.attempts == 2
    assert (run / "workspace" / "VERSION").read_text().strip() == "0.5.0"
    store_py = (run / "workspace" / "linkly" / "store.py").read_text()
    assert "ALTER TABLE urls ADD COLUMN expires_at" in store_py


def test_ambiguous_replans_after_answer_and_reuses_finished_work(driver):
    run = driver.start("ambiguous")
    assert driver.execute(run) == "waiting"
    assert driver.node(run, "clarify.Q-PRIVACY").status == NodeStatus.WAITING
    assert driver.node(run, "perf.redirect_cache").status == NodeStatus.SUCCEEDED
    assert driver.drive(run, answers={"Q-PRIVACY": "hashed"}) == "succeeded"
    m = driver.metrics(run)
    assert m["replans"] == 1 and m["reused_nodes"] == 2
    assert driver.node(run, "clarify.Q-PRIVACY").status == NodeStatus.SKIPPED
    assert RunState(run).store.get("test_report")["failed"] == 0


def test_choosing_raw_ip_storage_is_stopped_by_the_privacy_guardrail(driver):
    run = driver.start("ambiguous")
    assert driver.drive(run, answers={"Q-PRIVACY": "raw"}) == "failed"
    node = driver.node(run, "analytics.unique_visitors_raw")
    assert node.status == NodeStatus.FAILED and "PII-001" in node.error
    assert "client_ip" not in (run / "workspace" / "shortener" / "db.py").read_text()
    assert driver.node(run, "release").status == NodeStatus.BLOCKED


def test_rejected_approval_rolls_back_and_halts(driver):
    run = driver.start("brownfield")
    assert driver.drive(run, reject={"impl.expiry"}) == "failed"
    assert driver.node(run, "impl.expiry").error == "rejected by human approver"
    assert "expires_at" not in (run / "workspace" / "linkly" / "store.py").read_text()
    assert driver.node(run, "release").status == NodeStatus.BLOCKED


def test_safe_stop_then_resume(driver):
    run = driver.start("greenfield")
    (run / "STOP").touch()
    assert driver.execute(run) == "stopped"
    assert not (run / "STOP").exists()
    assert driver.drive(run) == "succeeded"


def test_attempt_budget_triggers_safe_stop(driver):
    run = driver.start("brownfield", budgets={"max_attempts": 3, "max_active_s": 600})
    assert driver.execute(run) == "stopped"
    assert "budget" in RunState(run).stop_reason


def test_crash_between_apply_and_commit_is_rolled_back_on_resume(driver):
    run = driver.start("greenfield")
    (run / "workspace" / "half_written.py").write_text("partial")
    (run / "undo" / "scaffold.inflight.json").write_text(json.dumps({"half_written.py": None}))
    orch = Orchestrator(run)
    orch._recover_inflight()
    assert not (run / "workspace" / "half_written.py").exists()


class FailingLLM:
    async def structured(self, **_):
        raise AgentError("simulated provider outage")


def test_llm_outage_falls_back_to_deterministic_agents(driver):
    run = driver.start("brownfield", provider="claude")
    assert driver.drive(run, llm=FailingLLM()) == "succeeded"
    attempts = [e for e in audit.read(run / "audit.jsonl") if e["event"] == "attempt.start"]
    agents = {(e["node"], e["data"]["agent"]) for e in attempts}
    assert ("requirements", "requirements.llm") in agents and ("requirements", "requirements") in agents
    assert ("impl.url_validation", "implement.llm") in agents
    assert ("impl.url_validation", "implement") in agents


def test_parallel_branches_actually_overlap(driver):
    run = driver.start("greenfield")
    asyncio.run(Orchestrator(run, "pause", 4).execute())
    spans = {}
    for e in audit.read(run / "audit.jsonl"):
        if e["event"] in ("attempt.start", "attempt.end") and e["node"].startswith("impl."):
            spans.setdefault(e["node"], []).append(e["ts"])
    windows = sorted((s[0], s[-1]) for s in spans.values())
    assert any(b[0] < a[1] for a, b in zip(windows, windows[1:]))
