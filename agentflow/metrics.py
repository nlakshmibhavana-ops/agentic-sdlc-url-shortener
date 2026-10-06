"""Reliability metrics, derived from the audit trail (the single source of truth)."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from .audit import read


def compute(audit_path: Path) -> dict[str, Any]:
    events = read(audit_path)
    # An attempt parked for a human (approval) is neither a success nor a failure.
    attempts = [
        e
        for e in events
        if e["event"] == "attempt.end" and e["data"].get("result") in ("succeeded", "failed")
    ]
    ok_attempts = [e for e in attempts if e["data"].get("result") == "succeeded"]
    applied = [e for e in events if e["event"] == "change.applied"]
    rollbacks = [e for e in events if e["event"] == "change.rolled_back"]
    node_ok = {e["node"] for e in events if e["event"] == "node.succeeded"}
    node_failed = {e["node"] for e in events if e["event"] == "node.failed"}
    retries = [e for e in events if e["event"] == "attempt.retry"]

    # MTTR: first failed attempt of a node -> that node's next success.
    first_failure: dict[str, float] = {}
    recoveries: list[float] = []
    for e in events:
        if e["event"] == "attempt.end" and e["data"].get("result") == "failed":
            first_failure.setdefault(e["node"], e["ts"])
        # Recovered = the first later attempt that passes its gates. Time a verified change
        # then spends waiting for a human approver is reported separately, not as MTTR.
        recovered = e["event"] == "attempt.end" and e["data"].get("result") in ("succeeded", "waiting")
        if recovered and e["node"] in first_failure:
            recoveries.append(e["ts"] - first_failure.pop(e["node"]))

    sessions = []
    start = None
    for e in events:
        if e["event"] == "run.session.start":
            start = e["ts"]
        elif e["event"] == "run.session.end" and start is not None:
            sessions.append(e["ts"] - start)
            start = None
    approvals = [e for e in events if e["event"] == "approval.decided"]
    stage_latency: dict[str, float] = {}
    for e in attempts:
        stage_latency[e["node"]] = stage_latency.get(e["node"], 0) + e["data"].get("duration_ms", 0)

    return {
        "nodes_succeeded": len(node_ok),
        "nodes_failed": len(node_failed - node_ok),
        "node_success_rate": round(len(node_ok) / max(1, len(node_ok | node_failed)), 3),
        "attempts": len(attempts),
        "attempt_success_rate": round(len(ok_attempts) / max(1, len(attempts)), 3),
        "retries": len(retries),
        "changes_applied": len(applied),
        "rollbacks": len(rollbacks),
        "rollback_rate": round(len(rollbacks) / max(1, len(applied)), 3),
        "mttr_s": round(mean(recoveries), 3) if recoveries else None,
        "recoveries": len(recoveries),
        "replans": sum(1 for e in events if e["event"] == "plan.revised"),
        "invalidations": sum(1 for e in events if e["event"] == "node.invalidated"),
        "reused_nodes": sum(1 for e in events if e["event"] == "node.reused"),
        "policy_blocks": sum(1 for e in events if e["event"] == "policy.blocked"),
        "approvals": len(approvals),
        "approval_wait_s": round(mean(a["data"]["waited_s"] for a in approvals), 1) if approvals else None,
        "active_time_s": round(sum(sessions), 3),
        "wall_time_s": round(events[-1]["ts"] - events[0]["ts"], 3) if events else 0,
        "sessions": len(sessions),
        "llm_tokens": sum(e["data"].get("tokens", 0) for e in attempts),
        "stage_latency_ms": {k: round(v, 1) for k, v in stage_latency.items()},
    }


def aggregate(runs_dir: Path) -> dict[str, Any]:
    rows = []
    for state in sorted(runs_dir.glob("*/state.json")):
        meta = json.loads(state.read_text())
        metrics = compute(state.parent / "audit.jsonl")
        rows.append(
            {
                "run_id": meta["run_id"],
                "scenario": meta["scenario"]["id"],
                "status": meta["status"],
                **metrics,
            }
        )
    finished = [r for r in rows if r["status"] in ("succeeded", "failed", "stopped")]
    mttrs = [r["mttr_s"] for r in rows if r["mttr_s"] is not None]
    return {
        "runs": len(rows),
        "run_success_rate": round(
            sum(r["status"] == "succeeded" for r in finished) / max(1, len(finished)), 3
        ),
        "mean_active_time_s": round(mean(r["active_time_s"] for r in rows), 2) if rows else None,
        "total_retries": sum(r["retries"] for r in rows),
        "total_rollbacks": sum(r["rollbacks"] for r in rows),
        "rollback_rate": round(
            sum(r["rollbacks"] for r in rows) / max(1, sum(r["changes_applied"] for r in rows)), 3
        ),
        "mean_mttr_s": round(mean(mttrs), 3) if mttrs else None,
        "per_run": rows,
    }
