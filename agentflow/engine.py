"""The orchestrator: runs the delivery graph under governance.

Per node:  entry gates -> agent (bounded retries, fallback agent) -> policy check ->
           human approval (if high impact) -> apply under the workspace lock ->
           exit gates -> commit, or roll back and retry with the failure as feedback.

Across nodes: ready nodes run in parallel up to max_parallel; join stages synchronise;
changed artifacts invalidate their consumers; a changed plan is merged into the live
graph (dynamic re-planning); a failed critical node, a STOP file, SIGINT or an exhausted
budget triggers a safe stop. State is persisted after every step, so any run can be
resumed after a pause for human input, a stop, or a crash.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from pathlib import Path
from typing import Any

from . import gates
from . import scenario as scenario_mod
from .agents import REGISTRY, AgentContext
from .approvals import PENDING, REJECTED, ApprovalBroker
from .audit import AuditLog
from .context import ContextStore
from .graph import Graph
from .models import (
    AgentError,
    AgentResult,
    Decision,
    Impact,
    Node,
    NodeStatus,
    stable_hash,
)
from .policy import PolicyEngine
from .workspace import UndoLog, Workspace, WorkspaceError, diff_stats

MUTATING_KINDS = {"task"}


class RunState:
    """Everything needed to resume a run, persisted as runs/<id>/state.json."""

    def __init__(self, run_dir: Path) -> None:
        self.dir = run_dir
        data = json.loads((run_dir / "state.json").read_text())
        self.run_id: str = data["run_id"]
        self.scenario: dict[str, Any] = data["scenario"]
        self.status: str = data["status"]
        self.stop_reason: str | None = data.get("stop_reason")
        self.baseline: set[str] = set(data.get("baseline", []))
        self.graph = Graph(Node.from_dict(n) for n in data["nodes"])
        self.store = ContextStore.from_dict(data["context"])
        self.created_at: float = data["created_at"]

    def save(self) -> None:
        data = {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "baseline": sorted(self.baseline),
            "created_at": self.created_at,
            "nodes": [self.graph[nid].to_dict() for nid in self.graph.topological_order()],
            "context": self.store.to_dict(),
        }
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(data, indent=1, default=str))
        tmp.replace(self.dir / "state.json")  # atomic: a crash never leaves half a state file

    @property
    def workspace(self) -> Workspace:
        return Workspace(self.dir / "workspace")


def create_run(scenario: dict[str, Any], runs_dir: Path, run_id: str | None = None) -> Path:
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S-") + scenario["id"]
    run_dir = runs_dir / run_id
    if run_dir.exists():
        raise FileExistsError(f"run {run_id} already exists")
    for sub in ("workspace", "changes", "undo", "artifacts"):
        (run_dir / sub).mkdir(parents=True)
    ws = Workspace(run_dir / "workspace")
    base = scenario.get("base")
    if base:
        source = (
            scenario_mod.ROOT / base
            if not base.startswith("playbook:")
            else (scenario_mod.ROOT / "agentflow" / "playbooks" / base.split(":", 1)[1] / "files")
        )
        ws.seed(source)
    store = ContextStore()
    store.put("requirement_text", scenario["requirement"], "scenario")
    store.put("human_answers", {}, "scenario")
    graph = Graph(scenario_mod.stage_nodes(scenario))
    data = {
        "run_id": run_id,
        "scenario": scenario,
        "status": "created",
        "stop_reason": None,
        "baseline": ws.files(),
        "created_at": time.time(),
        "nodes": [n.to_dict() for n in graph],
        "context": store.to_dict(),
    }
    (run_dir / "state.json").write_text(json.dumps(data, indent=1, default=str))
    AuditLog(run_dir / "audit.jsonl", run_id).record(
        "run.created",
        scenario=scenario["id"],
        provider=scenario.get("provider", "deterministic"),
        baseline_files=len(data["baseline"]),
    )
    return run_dir


class Orchestrator:
    def __init__(
        self,
        run_dir: Path,
        approval_mode: str = "pause",
        max_parallel: int = 4,
        llm: Any = None,
        on_event: Any = None,
    ) -> None:
        self.state = RunState(run_dir)
        self.dir = run_dir
        self.audit = AuditLog(run_dir / "audit.jsonl", self.state.run_id)
        self.audit.listener = on_event
        self.broker = ApprovalBroker(run_dir / "approvals.json", self.audit, approval_mode)
        self.policy = PolicyEngine(self.state.scenario.get("policy"), self.state.baseline)
        self.max_parallel = max_parallel
        self.llm = llm
        self.lock = asyncio.Lock()
        self.halt_reason: str | None = None
        self._stop = asyncio.Event()
        self._attempts_this_session = 0
        self._session_started = 0.0

    # ------------------------------------------------------------------ public API

    @property
    def graph(self) -> Graph:
        return self.state.graph

    @property
    def store(self) -> ContextStore:
        return self.state.store

    def submit_answer(self, question: str, option: str, by: str) -> None:
        """A human answer is just a new upstream artifact; invalidation does the rest."""
        answers = dict(self.store.get("human_answers") or {})
        answers[question] = {"option": option, "by": by, "ts": time.time()}
        self.store.put("human_answers", answers, f"human:{by}")
        self.audit.record("human.answered", actor=f"human:{by}", question=question, option=option)
        self.store.add_decision(
            Decision(
                title=f"{question} answered: {option}",
                rationale="human clarification",
                decided_by=f"human:{by}",
                node="requirements",
            )
        )
        self._propagate("human_answers")
        self.state.save()

    async def execute(self) -> str:
        st = self.state
        self._session_started = time.time()
        self.audit.record("run.session.start", status=st.status)
        self._recover_inflight()
        for node in self.graph:
            if node.status in (NodeStatus.WAITING, NodeStatus.STOPPED, NodeStatus.RUNNING):
                node.status, node.wait_reason = NodeStatus.PENDING, None
        st.status, st.stop_reason = "running", None
        st.save()
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, self.request_stop, "SIGINT")
        except (NotImplementedError, RuntimeError):
            pass
        try:
            await self._loop()
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError):
                pass
        st.status = self._final_status()
        st.save()
        self.audit.record(
            "run.session.end",
            status=st.status,
            stop_reason=st.stop_reason,
            waiting=[n.id for n in self.graph if n.status == NodeStatus.WAITING],
        )
        from .report import write_report  # local import: report depends on engine types

        write_report(self.dir)
        return st.status

    def request_stop(self, reason: str = "operator") -> None:
        self.halt_reason = self.halt_reason or f"safe-stop requested ({reason})"
        self._stop.set()

    # ------------------------------------------------------------------ scheduling

    async def _loop(self) -> None:
        running: dict[asyncio.Task, Node] = {}
        while True:
            self._check_stop_conditions()
            if not self.halt_reason:
                for node in self.graph.ready():
                    if len(running) >= self.max_parallel:
                        break
                    node.status = NodeStatus.RUNNING
                    running[asyncio.create_task(self._run_node(node))] = node
                self.state.save()
            if not running:
                break
            if self._stop.is_set():
                for task in running:
                    task.cancel()
            done, _ = await asyncio.wait(running, timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                node = running.pop(task)
                if task.cancelled():
                    node.status = NodeStatus.STOPPED
                    self.audit.record("node.stopped", node=node.id)
                elif task.exception():
                    node.status, node.error = NodeStatus.FAILED, repr(task.exception())
                    self.audit.record("node.failed", node=node.id, error=node.error)
                    self._on_failure(node)
                self.state.save()

    def _check_stop_conditions(self) -> None:
        if (self.dir / "STOP").exists():
            (self.dir / "STOP").unlink()
            self.request_stop("STOP file")
        budgets = self.state.scenario.get("budgets", {})
        used = sum(n.attempts for n in self.graph)
        if used >= budgets.get("max_attempts", 10**9):
            self.request_stop(f"attempt budget {budgets['max_attempts']} exhausted")
        if time.time() - self._session_started > budgets.get("max_active_s", 10**9):
            self.request_stop(f"time budget {budgets['max_active_s']}s exhausted")
        if self.halt_reason and not self.state.stop_reason:
            self.state.stop_reason = self.halt_reason
            self.audit.record("run.safe_stop", reason=self.halt_reason)

    def _final_status(self) -> str:
        statuses = {n.status for n in self.graph}
        if any(n.status == NodeStatus.FAILED and n.critical for n in self.graph):
            return "failed"
        if self.halt_reason or NodeStatus.STOPPED in statuses:
            return "stopped"
        if NodeStatus.WAITING in statuses:
            return "waiting"
        if self.graph.is_finished():
            return "succeeded" if NodeStatus.FAILED not in statuses else "degraded"
        return "stalled"

    # ------------------------------------------------------------------ node execution

    def _gctx(self, node: Node) -> gates.GateContext:
        return gates.GateContext(node, self.store, self.state.workspace, self.state.scenario, self.dir)

    async def _run_node(self, node: Node) -> None:
        node.started_at = node.started_at or time.time()
        for spec in node.entry_gates:
            result = await gates.evaluate(spec, self._gctx(node))
            self.audit.record(
                "gate.entry", node=node.id, gate=spec, passed=result.passed, detail=result.detail
            )
            if not result.passed:
                if result.human:
                    return self._wait(node, result.detail)
                node.status, node.error = NodeStatus.FAILED, result.detail
                self.audit.record("node.failed", node=node.id, error=result.detail)
                return self._on_failure(node)

        if node.agent == "none":  # a pure human checkpoint whose gate has now opened
            node.attempts += 1
            return self._succeed(node, AgentResult(), {}, [])

        if node.pending and await self._resume_pending(node):
            return None

        consumed = {a: self.store.version(a) for a in node.inputs if self.store.has(a)}
        feedback: str | None = None
        use_fallback = False
        while node.attempts < node.max_attempts:
            if self._stop.is_set():
                node.status = NodeStatus.STOPPED
                return None
            attempt = node.attempts + 1
            last = attempt == node.max_attempts
            agent_name = (
                node.fallback_agent if (node.fallback_agent and (use_fallback or last)) else node.agent
            )
            outcome = await self._attempt(node, attempt, agent_name, consumed, feedback)
            if outcome["result"] == "succeeded":
                return None
            if outcome["result"] in ("waiting", "rejected"):
                return None
            node.attempts += 1
            self._attempts_this_session += 1
            feedback = outcome.get("feedback")
            use_fallback = use_fallback or outcome.get("agent_error", False)
            if node.attempts < node.max_attempts:
                self.audit.record(
                    "attempt.retry", node=node.id, next_attempt=node.attempts + 1, reason=feedback
                )
                await asyncio.sleep(min(0.05 * 2**node.attempts, 2.0))  # bounded backoff
        node.status, node.error = NodeStatus.FAILED, feedback
        self.audit.record("node.failed", node=node.id, error=feedback, attempts=node.attempts)
        self._on_failure(node)
        return None

    async def _attempt(
        self, node: Node, attempt: int, agent_name: str, consumed: dict[str, int], feedback: str | None
    ) -> dict[str, Any]:
        result_info: dict[str, Any] = {"result": "failed"}
        with self.audit.span("attempt", node.id, attempt=attempt, agent=agent_name) as (span, out):
            ctx = AgentContext(
                node=node,
                store=self.store,
                workspace=self.state.workspace,
                scenario=self.state.scenario,
                attempt=attempt,
                run_dir=self.dir,
                lock=self.lock,
                llm=self.llm,
                extras={"feedback": feedback, "baseline_files": self.state.baseline},
            )
            try:
                agent = REGISTRY[agent_name]()
                result = await asyncio.wait_for(agent.run(ctx), timeout=node.params.get("timeout", 900))
            except (AgentError, TimeoutError, KeyError, ValueError, OSError) as exc:
                msg = f"{agent_name}: {exc!r}"
                out.update(result="failed", reason=msg)
                return {"result": "failed", "feedback": msg, "agent_error": True}
            out["tokens"] = result.tokens
            for note in result.notes:
                self.audit.record(
                    "agent.note", node=node.id, span_id=span, actor=f"agent:{agent_name}", note=note
                )

            staged = self._stage_artifacts(node, result, consumed)
            undo: UndoLog | None = None
            async with self.lock:
                if result.changeset is not None:
                    applied = await self._apply_change(node, result, attempt, span)
                    if applied["result"] != "applied":
                        self._unstage(staged)
                        out.update(result=applied["result"], reason=applied.get("feedback"))
                        return applied
                    undo = applied["undo"]
                gate_results = [await gates.evaluate(g, self._gctx(node)) for g in node.exit_gates]
                for g in gate_results:
                    self.audit.record(
                        "gate.exit", node=node.id, span_id=span, gate=g.gate, passed=g.passed, detail=g.detail
                    )
                failed = [g for g in gate_results if not g.passed]
                if failed:
                    if undo is not None:
                        self.state.workspace.rollback(undo)
                        self._drop_undo(node, inflight=True)
                        self.audit.record(
                            "change.rolled_back", node=node.id, span_id=span, paths=sorted(undo.before)
                        )
                    self._unstage(staged)
                    msg = "; ".join(f"{g.gate}: {g.detail}" for g in failed)
                    out.update(result="failed", reason=msg)
                    return {"result": "failed", "feedback": msg}
                if undo is not None and applied["reasons"]:
                    # Humans approve verified changes: the gates above are the evidence.
                    evidence = "\n".join(
                        f"[{'x' if g.passed else ' '}] {g.gate}: {g.detail[:160]}" for g in gate_results
                    )
                    summary = f"{applied['summary']}\nValidation evidence:\n{evidence}"
                    decision = self.broker.check(node.id, applied["digest"], summary, applied["reasons"])
                    if decision == PENDING:
                        self._park_for_approval(node, applied, summary, consumed, staged, result)
                        out["result"] = "waiting"
                        return {"result": "waiting"}
                    if decision == REJECTED:
                        self._reject(node, undo, staged, applied["digest"])
                        out["result"] = "rejected"
                        return {"result": "rejected"}
                if undo is not None:
                    self._commit_undo(node)
            node.attempts += 1
            out["result"] = "succeeded"
            result_info["result"] = "succeeded"
        self._succeed(node, result, consumed, [name for name, _ in staged])
        return result_info

    async def _apply_change(self, node: Node, result: AgentResult, attempt: int, span: str) -> dict[str, Any]:
        cs = result.changeset
        ws = self.state.workspace
        try:
            planned = ws.preview(cs)
        except WorkspaceError as exc:
            return {"result": "failed", "feedback": f"change does not apply: {exc}"}
        verdict = self.policy.evaluate(node, cs, planned)
        self.audit.record(
            "policy.evaluated",
            node=node.id,
            span_id=span,
            digest=cs.digest(),
            findings=[vars(f) for f in verdict.findings],
        )
        if verdict.blocking:
            msg = "; ".join(f"{f.rule} {f.path}: {f.message}" for f in verdict.blocking)
            self.audit.record("policy.blocked", node=node.id, span_id=span, reason=msg)
            return {"result": "failed", "feedback": f"policy blocked: {msg}"}
        reasons = [f"{f.rule} {f.path}: {f.message}" for f in verdict.approvals]
        if node.impact == Impact.HIGH:
            reasons.insert(0, f"high-impact stage '{node.id}'")
        self._write_undo(node, UndoLog({p: b for p, (b, _) in planned.items()}), inflight=True)
        undo, diff = ws.apply(cs)
        (self.dir / "changes" / f"{node.id}.a{attempt}.diff").write_text(diff)
        added, removed = diff_stats(diff)
        self.audit.record(
            "change.applied",
            node=node.id,
            span_id=span,
            digest=cs.digest(),
            summary=cs.summary,
            candidate=cs.candidate,
            paths=cs.paths(),
            added=added,
            removed=removed,
        )
        return {
            "result": "applied",
            "undo": undo,
            "reasons": reasons,
            "digest": cs.digest(),
            "summary": self._approval_summary(node, cs, planned),
        }

    def _park_for_approval(
        self,
        node: Node,
        applied: dict[str, Any],
        summary: str,
        consumed: dict[str, int],
        staged: list[tuple[str, bool]],
        result: AgentResult,
    ) -> None:
        (self.dir / "undo" / f"{node.id}.inflight.json").replace(
            self.dir / "undo" / f"{node.id}.pending.json"
        )
        node.pending = {
            "digest": applied["digest"],
            "summary": summary,
            "reasons": applied["reasons"],
            "consumed": consumed,
            "produced": [name for name, _ in staged],
            "decisions": [vars(d) for d in result.decisions],
        }
        self._wait(node, "approval required: " + "; ".join(applied["reasons"]))

    def _reject(self, node: Node, undo: UndoLog, staged: list[tuple[str, bool]], digest: str) -> None:
        self.state.workspace.rollback(undo)
        for name in ("inflight", "pending"):
            (self.dir / "undo" / f"{node.id}.{name}.json").unlink(missing_ok=True)
        self.audit.record(
            "change.rolled_back", node=node.id, paths=sorted(undo.before), reason="rejected by human approver"
        )
        self._unstage(staged)
        node.status, node.error, node.pending = NodeStatus.FAILED, "rejected by human approver", None
        self.audit.record("node.rejected", node=node.id, digest=digest)
        self._on_failure(node)

    async def _resume_pending(self, node: Node) -> bool:
        """Continue a node parked for approval. Returns True if it was handled."""
        pa = node.pending
        decision = self.broker.check(node.id, pa["digest"], pa["summary"], pa["reasons"])
        if decision == PENDING:
            self._wait(node, "approval required: " + "; ".join(pa["reasons"]))
            return True
        path = self.dir / "undo" / f"{node.id}.pending.json"
        undo = UndoLog(json.loads(path.read_text()))
        if decision == REJECTED:
            self._reject(node, undo, [], pa["digest"])
            return True
        async with self.lock:  # re-check: evidence must still hold at the moment of approval
            results = [await gates.evaluate(g, self._gctx(node)) for g in node.exit_gates]
            for g in results:
                self.audit.record(
                    "gate.exit", node=node.id, gate=g.gate, passed=g.passed, detail=g.detail, recheck=True
                )
            if not all(g.passed for g in results):
                self.state.workspace.rollback(undo)
                path.unlink()
                self.audit.record(
                    "change.rolled_back",
                    node=node.id,
                    paths=sorted(undo.before),
                    reason="evidence no longer holds after approval",
                )
                node.pending = None
                return False
            path.replace(self.dir / "undo" / f"{node.id}.json")
        node.attempts += 1
        self.audit.record("approval.applied", node=node.id, digest=pa["digest"])
        node.pending = None
        result = AgentResult(decisions=[Decision(**d) for d in pa["decisions"]])
        self._succeed(node, result, pa["consumed"], pa["produced"])
        return True

    def _approval_summary(self, node: Node, cs, planned) -> str:
        lines = [
            f"{node.title}",
            f"change: {cs.summary} (digest {cs.digest()})",
            f"files: {', '.join(sorted(planned))}",
        ]
        readiness = self.store.get("release_readiness")
        if node.id == "release" and readiness:
            lines += [
                f"[{'x' if c['passed'] else ' '}] {c['check']}: {c['detail']}" for c in readiness["checks"]
            ]
        return "\n".join(lines)

    # ------------------------------------------------------------------ outcomes

    def _stage_artifacts(
        self, node: Node, result: AgentResult, consumed: dict[str, int]
    ) -> list[tuple[str, bool]]:
        staged = []
        for name, content in result.artifacts.items():
            _, changed = self.store.put(name, content, node.id, consumed)
            staged.append((name, changed))
        return staged

    def _unstage(self, staged: list[tuple[str, bool]]) -> None:
        for name, changed in staged:
            if changed:
                self.store.artifacts[name].pop()

    def _succeed(
        self, node: Node, result: AgentResult, consumed: dict[str, int], produced: list[str]
    ) -> None:
        node.status = NodeStatus.SUCCEEDED
        node.finished_at = time.time()
        node.error = node.wait_reason = None
        node.consumed = consumed
        node.produced = {a: self.store.version(a) for a in produced}
        node.fingerprint = stable_hash({"spec": node.spec_hash(), "inputs": consumed})
        node.runs += 1
        for decision in result.decisions:
            decision.node = decision.node or node.id
            decision.inputs = decision.inputs or consumed
            self.store.add_decision(decision)
        self.audit.record(
            "node.succeeded", node=node.id, attempts=node.attempts, produced=node.produced, run=node.runs
        )
        for name in produced:
            self._propagate(name)
        if "plan" in produced:
            self._merge_plan()
        if node.kind in MUTATING_KINDS or node.writes:
            self._invalidate_downstream(node, "workspace changed upstream")

    def _wait(self, node: Node, reason: str) -> None:
        node.status, node.wait_reason = NodeStatus.WAITING, reason
        self.audit.record("node.waiting", node=node.id, reason=reason)

    def _on_failure(self, node: Node) -> None:
        blocked = self.graph.block_downstream(node.id, f"upstream {node.id} failed")
        if blocked:
            self.audit.record("node.blocked_downstream", node=node.id, blocked=blocked)
        if node.critical and not self.halt_reason:
            self.halt_reason = f"critical node '{node.id}' failed: {node.error}"
            self.state.stop_reason = self.halt_reason
            self.audit.record("run.safe_stop", reason=self.halt_reason)

    # ------------------------------------------------------------------ invalidation & re-planning

    def _propagate(self, artifact: str) -> None:
        version = self.store.version(artifact)
        for consumer in self.graph.consumers_of(artifact):
            stale = consumer.consumed.get(artifact, 0) < version
            if consumer.status == NodeStatus.SUCCEEDED and stale:
                self._invalidate(consumer, f"input '{artifact}' changed to v{version}")
            elif consumer.status == NodeStatus.WAITING:
                consumer.status, consumer.wait_reason = NodeStatus.PENDING, None

    def _invalidate(self, node: Node, reason: str) -> None:
        if node.status != NodeStatus.SUCCEEDED:
            return
        node.status, node.attempts = NodeStatus.PENDING, 0
        self.audit.record("node.invalidated", node=node.id, reason=reason)

    def _invalidate_downstream(self, node: Node, reason: str) -> None:
        for nid in self.graph.downstream(node.id):
            child = self.graph[nid]
            if child.kind not in MUTATING_KINDS and child.kind != "checkpoint":
                self._invalidate(child, f"{reason} ({node.id})")

    def _merge_plan(self) -> None:
        plan = self.store.get("plan")
        version = self.store.version("plan")
        new_nodes = {t["id"]: scenario_mod.task_node(t, version) for t in plan["tasks"]}
        existing = {n.id: n for n in self.graph if n.kind in ("task", "checkpoint")}
        added, changed, removed, reused = [], [], [], []
        for nid, fresh in new_nodes.items():
            old = existing.get(nid)
            if (
                old
                and old.spec_hash() == fresh.spec_hash()
                and old.deps == fresh.deps
                and old.entry_gates == fresh.entry_gates
            ):
                if old.status == NodeStatus.SKIPPED:
                    old.status = NodeStatus.PENDING
                elif old.status == NodeStatus.SUCCEEDED and version > 1:
                    reused.append(nid)
                    self.audit.record("node.reused", node=nid, plan_version=version)
                continue
            if old:
                if old.status == NodeStatus.SUCCEEDED and not self._revert(old, "task changed in re-plan"):
                    continue  # kept as-is; a decision record asks a human to review it
                changed.append(nid)
            else:
                added.append(nid)
            self.graph.nodes[nid] = fresh
        for nid, old in existing.items():
            if nid not in new_nodes and old.status != NodeStatus.SKIPPED:
                if old.status == NodeStatus.SUCCEEDED and not self._revert(old, "task removed in re-plan"):
                    continue
                old.status = NodeStatus.SKIPPED
                removed.append(nid)
                self.audit.record("node.superseded", node=nid, plan_version=version)
        active = sorted(
            n.id for n in self.graph if n.kind in ("task", "checkpoint") and n.status != NodeStatus.SKIPPED
        )
        for node in self.graph:
            if node.params.get("join"):
                node.deps = ["plan", *active]
                if added or changed:
                    self._invalidate(node, "plan changed")
        self.graph.validate()
        if added or changed:
            for node in self.graph:
                if (
                    node.status == NodeStatus.SUCCEEDED
                    and node.kind == "stage"
                    and node.id in ("readiness", "release")
                ):
                    self._invalidate(node, "plan changed")
        event = "plan.created" if version == 1 else "plan.revised"
        self.audit.record(
            event,
            node="plan",
            plan_version=version,
            added=added,
            changed=changed,
            removed=removed,
            reused=reused,
        )
        if version > 1:
            self.store.add_decision(
                Decision(
                    title=f"Re-plan to v{version}",
                    rationale=f"upstream change; added {added}, changed {changed}, removed {removed}, "
                    f"kept {reused} without re-running",
                    node="plan",
                    decided_by="orchestrator",
                )
            )

    def _revert(self, node: Node, reason: str) -> bool:
        """Undo a committed change. False if it cannot be undone safely."""
        path = self.dir / "undo" / f"{node.id}.json"
        if node.status != NodeStatus.SUCCEEDED or not path.exists():
            return True
        undo = UndoLog(json.loads(path.read_text()))
        later = [
            n
            for n in self.graph
            if n.id != node.id
            and n.status == NodeStatus.SUCCEEDED
            and (self.dir / "undo" / f"{n.id}.json").exists()
            and set(json.loads((self.dir / "undo" / f"{n.id}.json").read_text())) & set(undo.before)
            and (n.finished_at or 0) > (node.finished_at or 0)
        ]
        if later:
            self.store.add_decision(
                Decision(
                    title=f"Could not auto-revert {node.id}",
                    rationale=f"later changes by {[n.id for n in later]} touch the same files; "
                    "left in place for human review",
                    node=node.id,
                    decided_by="orchestrator",
                )
            )
            self.audit.record(
                "change.revert_refused", node=node.id, reason=reason, blocked_by=[n.id for n in later]
            )
            return False
        self.state.workspace.rollback(undo)
        path.unlink()
        self.audit.record("change.reverted", node=node.id, reason=reason, paths=sorted(undo.before))
        return True

    # ------------------------------------------------------------------ undo logs (crash safety)

    def _write_undo(self, node: Node, undo: UndoLog, inflight: bool) -> None:
        name = f"{node.id}.inflight.json" if inflight else f"{node.id}.json"
        (self.dir / "undo" / name).write_text(json.dumps(undo.before))

    def _commit_undo(self, node: Node) -> None:
        (self.dir / "undo" / f"{node.id}.inflight.json").replace(self.dir / "undo" / f"{node.id}.json")

    def _drop_undo(self, node: Node, inflight: bool) -> None:
        name = f"{node.id}.inflight.json" if inflight else f"{node.id}.json"
        (self.dir / "undo" / name).unlink(missing_ok=True)

    def _recover_inflight(self) -> None:
        """A crash between apply and commit leaves an inflight undo log: roll it back."""
        for path in sorted((self.dir / "undo").glob("*.inflight.json")):
            self.state.workspace.rollback(UndoLog(json.loads(path.read_text())))
            path.unlink()
            self.audit.record(
                "change.rolled_back", node=path.name.split(".inflight")[0], reason="crash recovery"
            )
