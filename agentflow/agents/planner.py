"""Task decomposition: turn requirements + design into an executable task graph.

The plan is an artifact like any other. When requirements change (e.g. a human answers a
question) the planner re-runs and the engine merges the new plan into the live graph:
unchanged tasks are kept, changed ones are re-run, removed ones are skipped.
"""

from __future__ import annotations

from typing import Any

from .. import playbooks
from ..models import AgentError, AgentResult, Decision
from .base import Agent, AgentContext


def _reaches(deps: dict[str, set[str]], a: str, b: str) -> bool:
    """True if a (transitively) depends on b."""
    stack, seen = [a], set()
    while stack:
        cur = stack.pop()
        if cur == b:
            return True
        if cur not in seen:
            seen.add(cur)
            stack.extend(deps.get(cur, ()))
    return False


def _caps_for_question(qid: str) -> set[str]:
    from .. import knowledge

    q = next((x for x in knowledge.QUESTIONS if x["id"] == qid), None)
    return {c for opt in (q or {}).get("options", {}).values() for c in opt["adds"]}


def regression_for(writes: list[str], impact: dict[str, Any]) -> set[str]:
    """Existing tests that exercise (transitively) any module this task changes."""
    from .codebase import reverse_closure

    changed = {
        w[:-3].replace("/", ".").removesuffix(".__init__")
        for w in writes
        if w.endswith(".py") and not w.startswith("tests/")
    }
    closure = reverse_closure(impact["import_graph"], changed & set(impact["import_graph"]))
    return {t for t, mods in impact.get("test_map", {}).items() if set(mods) & closure}


def make_plan(
    playbook: str, req: dict[str, Any], impact: dict[str, Any] | None, llm_implement: bool = False
) -> tuple[dict[str, Any], list[Decision]]:
    book = playbooks.load(playbook)
    caps = {c["id"] for c in req["capabilities"]}
    decisions: list[Decision] = []
    selected = [t for t in book["tasks"] if set(t.get("capabilities", [])) & (caps | {"platform"})]
    ids = {t["id"] for t in selected}
    deps = {t["id"]: {d for d in t.get("deps", []) if d in ids} for t in selected}

    # Serialise tasks whose write sets overlap and that are not already ordered.
    for i, a in enumerate(selected):
        for b in selected[i + 1 :]:
            overlap = sorted(set(a.get("writes", [])) & set(b.get("writes", [])))
            if overlap and not _reaches(deps, b["id"], a["id"]) and not _reaches(deps, a["id"], b["id"]):
                deps[b["id"]].add(a["id"])
                decisions.append(
                    Decision(
                        title=f"Serialise {b['id']} after {a['id']}",
                        rationale=f"Both change {overlap}; running them in parallel could produce "
                        "conflicting edits, so they are ordered like a merge queue.",
                        alternatives=["Run in parallel and merge (conflict-prone)"],
                    )
                )

    tasks = []
    for t in selected:
        verify = list(t.get("verify", []))
        if impact and verify:
            verify = sorted(set(verify) | regression_for(t.get("writes", []), impact))
        agent = t.get("agent", "implement")
        if llm_implement and agent == "implement":
            agent = "implement.llm"
        tasks.append(
            {
                "id": t["id"],
                "title": t["title"],
                "agent": agent,
                "capabilities": t.get("capabilities", []),
                "deps": sorted(deps[t["id"]]),
                "writes": t.get("writes", []),
                "verify": verify,
                "impact": t.get("impact", "low"),
                "candidates": playbooks.candidate_count(playbook, t["id"]),
            }
        )

    # Work that depends on an unanswered blocking question becomes an explicit human gate.
    for qid in req.get("blocking_open", []):
        tasks.append(
            {
                "id": f"clarify.{qid}",
                "title": f"Await human decision on {qid}",
                "agent": "none",
                "capabilities": [],
                "deps": [],
                "writes": [],
                "verify": [],
                "impact": "low",
                "gate": f"clarified:{qid}",
                "candidates": 1,
            }
        )

    trace = {cap: [t["id"] for t in tasks if cap in t["capabilities"]] for cap in sorted(caps)}
    existing: dict[str, list[str]] = {}
    if impact:  # brownfield: a capability the current code already provides needs no task
        for cap, ts in trace.items():
            modules = impact["impacted"].get(cap, {}).get("modules", [])
            if not ts and modules:
                existing[cap] = modules
                trace[cap] = [f"existing:{m}" for m in modules]
    pending_caps = {c for q in req.get("blocking_open", []) for c in _caps_for_question(q)}
    uncovered = sorted(
        c for c, ts in trace.items() if not ts and c != "performance" and c not in pending_caps
    )
    if uncovered:
        raise AgentError(f"no task covers capabilities {uncovered}")
    if existing:
        decisions.append(
            Decision(
                title=f"No new work for {sorted(existing)}",
                rationale="Static analysis shows the existing code already implements these: "
                + "; ".join(f"{c} in {m}" for c, m in existing.items()),
                alternatives=["Re-implement (churn without a requirement)"],
            )
        )
    plan = {
        "playbook": playbook,
        "tasks": tasks,
        "traceability": trace,
        "satisfied_by_existing": existing,
        "uncovered": uncovered,
        "waiting_on": req.get("blocking_open", []),
    }
    return plan, decisions


class PlannerAgent(Agent):
    name = "planner"

    async def run(self, ctx: AgentContext) -> AgentResult:
        plan, decisions = make_plan(
            ctx.playbook,
            ctx.store.get("requirements"),
            ctx.store.get("impact_analysis"),
            llm_implement=ctx.scenario.get("provider") in ("claude", "ollama"),
        )
        notes = [f"{len(plan['tasks'])} tasks; waiting on {plan['waiting_on'] or 'nothing'}"]
        return AgentResult(artifacts={"plan": plan}, decisions=decisions, notes=notes)
