from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agentflow import scenario as scenario_mod
from agentflow.approvals import ApprovalBroker
from agentflow.audit import AuditLog
from agentflow.engine import Orchestrator, RunState, create_run


class Driver:
    """Plays the human: answers questions and approves (or rejects) until the run settles."""

    def __init__(self, runs: Path) -> None:
        self.runs = runs

    def start(self, name: str, **overrides) -> Path:
        sc = scenario_mod.load(name)
        sc.update(overrides)
        return create_run(sc, self.runs, f"{name}-test")

    def execute(self, run_dir: Path, llm=None) -> str:
        return asyncio.run(Orchestrator(run_dir, "pause", 4, llm).execute())

    def drive(
        self,
        run_dir: Path,
        answers: dict | None = None,
        reject: set | None = None,
        llm=None,
        max_sessions: int = 8,
    ) -> str:
        status = self.execute(run_dir, llm)
        for _ in range(max_sessions):
            if status != "waiting":
                return status
            st = RunState(run_dir)
            for qid in (st.store.get("requirements") or {}).get("blocking_open", []):
                Orchestrator(run_dir).submit_answer(qid, (answers or {})[qid], "tester")
            broker = ApprovalBroker(run_dir / "approvals.json", AuditLog(run_dir / "audit.jsonl", st.run_id))
            for req in broker.pending():
                broker.decide(req["node"], req["node"] not in (reject or set()), "tester")
            status = self.execute(run_dir, llm)
        return status

    @staticmethod
    def metrics(run_dir: Path) -> dict:
        return json.loads((run_dir / "metrics.json").read_text())

    @staticmethod
    def node(run_dir: Path, node_id: str):
        return RunState(run_dir).graph[node_id]


@pytest.fixture
def driver(tmp_path) -> Driver:
    return Driver(tmp_path / "runs")
