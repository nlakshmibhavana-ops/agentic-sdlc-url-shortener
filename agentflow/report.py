"""Human-readable run report (runs/<id>/report.md) plus exported artifacts.

Everything here is derived from state.json and the audit trail, so the report is a
view of the evidence, not a separate claim about it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from . import audit as audit_mod
from . import metrics
from .engine import RunState


def _ts(ts: float | None) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%H:%M:%S") if ts else ""


def _table(headers: list[str], rows: list[list[object]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |")
    return out


def write_report(run_dir: Path) -> Path:
    st = RunState(run_dir)
    events = audit_mod.read(run_dir / "audit.jsonl")
    m = metrics.compute(run_dir / "audit.jsonl")
    (run_dir / "metrics.json").write_text(json.dumps(m, indent=2))
    for name, versions in st.store.artifacts.items():
        (run_dir / "artifacts" / f"{name}.json").write_text(
            json.dumps([{k: v for k, v in vars(av).items()} for av in versions], indent=2, default=str)
        )
    store, sc = st.store, st.scenario
    req = store.get("requirements") or {}
    out = [
        f"# Run {st.run_id}",
        "",
        f"**Scenario:** {sc['id']}: {sc['title']}  ",
        f"**Status:** `{st.status}`" + (f" ({st.stop_reason})" if st.stop_reason else "") + "  ",
        f"**Provider:** {sc.get('provider', 'deterministic')} · **Playbook:** {sc['playbook']}",
        "",
        "## 1. Requirement",
        "",
        "```text",
        sc["requirement"].strip(),
        "```",
        "",
    ]

    if req:
        out += ["## 2. Requirement understanding", ""]
        out += _table(
            ["Capability", "Evidence in request", "Acceptance criteria"],
            [[c["id"], c["source"], "<br>".join(c["acceptance_criteria"])] for c in req["capabilities"]],
        )
        if req.get("questions"):
            out += ["", "**Ambiguities detected**", ""]
            rows = []
            for q in req["questions"]:
                res = req["resolved"].get(q["id"])
                status = (
                    f"{res['option']} ({res['source']})"
                    if res
                    else ("OPEN, blocking" if q["blocking"] else "open")
                )
                rows.append([q["id"], q["evidence"], q["text"], ", ".join(q["options"]), status])
            out += _table(["Id", "Trigger", "Question", "Options", "Resolution"], rows)
        for label, key in (("Assumptions", "assumptions"), ("Out of scope", "out_of_scope")):
            if req.get(key):
                out += ["", f"**{label}**", ""] + [f"- {x}" for x in req[key]]
        out.append("")

    impact = store.get("impact_analysis")
    if impact:
        out += [
            "## 3. Codebase reasoning (brownfield)",
            "",
            f"- Import graph: `{impact['import_graph']}`",
            f"- Blast radius: {impact['blast_radius']}",
            f"- Data at risk: {impact['data_at_risk']}",
            f"- Regression tests selected: {impact['regression_tests']}",
            "",
        ]
        out += _table(
            ["Capability", "Modules", "Routes", "Tables"],
            [[c, v["modules"], v["routes"], v["tables"]] for c, v in impact["impacted"].items()],
        )
        if impact["findings"]:
            out += ["", "**Latent defects found by static analysis**", ""]
            out += [
                f"- `{f['module']}.{f['function']}`: {f['kind']}: {f['detail']}" for f in impact["findings"]
            ]
        out.append("")

    design = store.get("design")
    if design:
        out += ["## 4. Design", "", f"Style: {design['style']}", ""]
        out += _table(
            ["ADR", "Decision", "Rationale", "Alternatives"],
            [[a["id"], a["title"], a["rationale"], "; ".join(a["alternatives"])] for a in design["adrs"]],
        )
        out.append("")

    plan = store.get("plan")
    if plan:
        out += [f"## 5. Plan (v{store.version('plan')}) and decomposition", ""]
        out += _table(
            ["Task", "Agent", "Depends on", "Writes", "Verified by", "Declared impact"],
            [
                [
                    t["id"],
                    t["agent"],
                    ", ".join(t["deps"]) or "-",
                    ", ".join(t["writes"]) or "-",
                    ", ".join(t["verify"]) or "-",
                    t["impact"],
                ]
                for t in plan["tasks"]
            ],
        )
        out += ["", "**Traceability (capability → tasks)**", ""]
        out += [f"- {c}: {ts or 'not covered'}" for c, ts in plan["traceability"].items()]
        out.append("")

    out += [
        "## 6. Orchestration graph",
        "",
        "```mermaid",
        st.graph.to_mermaid(),
        "```",
        "",
        "Rectangles are stages/tasks; diamonds are high-impact nodes that require human approval.",
        "",
    ]

    out += ["## 7. Execution", ""]
    out += _table(
        ["Node", "Kind", "Status", "Attempts", "Runs", "Origin", "Started", "Finished", "Note"],
        [
            [
                n.id,
                n.kind,
                n.status.value,
                n.attempts,
                n.runs,
                n.origin,
                _ts(n.started_at),
                _ts(n.finished_at),
                (n.wait_reason or n.error or "")[:160],
            ]
            for n in (st.graph[i] for i in st.graph.topological_order())
        ],
    )
    out += ["", "**Timeline (audit trail excerpts)**", ""]
    keep = {
        "run.session.start",
        "run.session.end",
        "attempt.start",
        "attempt.retry",
        "change.applied",
        "change.rolled_back",
        "change.reverted",
        "policy.blocked",
        "approval.requested",
        "approval.decided",
        "human.answered",
        "node.waiting",
        "node.invalidated",
        "node.reused",
        "node.superseded",
        "plan.created",
        "plan.revised",
        "node.failed",
        "run.safe_stop",
        "agent.note",
    }
    for e in events:
        if e["event"] in keep:
            data = {k: v for k, v in e["data"].items() if k not in ("findings",)}
            out.append(
                f"- `{_ts(e['ts'])}` **{e['event']}** {e['node'] or ''} {json.dumps(data, default=str)[:220]}"
            )
    out.append("")

    gates_rows = [
        [
            e["node"],
            e["event"].split(".")[1],
            e["data"]["gate"],
            "pass" if e["data"]["passed"] else "FAIL",
            e["data"]["detail"][:150],
        ]
        for e in events
        if e["event"].startswith("gate.")
    ]
    out += (
        ["## 8. Validation gates", ""]
        + _table(["Node", "Type", "Gate", "Result", "Detail"], gates_rows)
        + [""]
    )

    findings = [
        (e["node"], f) for e in events if e["event"] == "policy.evaluated" for f in e["data"]["findings"]
    ]
    out += ["## 9. Policy guardrails", ""]
    out += (
        _table(
            ["Node", "Rule", "Severity", "Path", "Message"],
            [[n, f["rule"], f["severity"], f["path"], f["message"]] for n, f in findings],
        )
        if findings
        else ["No policy findings on any change set."]
    )
    out.append("")

    path = run_dir / "approvals.json"
    data = json.loads(path.read_text()) if path.exists() else {"requests": {}, "decisions": {}}
    out += ["## 10. Human approvals", ""]
    if data["requests"]:
        out += _table(
            ["Node", "Subject", "Reasons", "Decision", "By", "Waited (s)"],
            [
                [
                    r["node"],
                    r["subject"],
                    "<br>".join(r["reasons"]),
                    data["decisions"].get(k, {}).get("status", "PENDING"),
                    data["decisions"].get(k, {}).get("by", ""),
                    data["decisions"].get(k, {}).get("waited_s", ""),
                ]
                for k, r in data["requests"].items()
            ],
        )
    else:
        out.append("None required.")
    out.append("")

    out += ["## 11. Decision log (lineage)", ""]
    out += _table(
        ["Id", "Node", "By", "Decision", "Rationale", "Inputs"],
        [[d.id, d.node, d.decided_by, d.title, d.rationale, d.inputs] for d in store.decisions],
    )
    out.append("")

    for name in ("test_report", "review_report", "release_readiness"):
        art = store.get(name)
        if art:
            out += [
                f"### {name}",
                "",
                "```json",
                json.dumps({k: v for k, v in art.items() if k != "tail"}, indent=1, default=str)[:2500],
                "```",
                "",
            ]

    release = store.latest("release") or store.latest("release_readiness")
    if release:
        out += ["## 12. Artifact lineage", "", f"Provenance of `{release.name}`:", ""]
        out += [
            f"{'  ' * x['depth']}- {x['artifact']} v{x['version']} ← {x['producer']}"
            for x in store.lineage(release.name)
        ]
        out.append("")

    out += (
        ["## 13. Reliability metrics", ""]
        + _table(["Metric", "Value"], [[k, v] for k, v in m.items() if k != "stage_latency_ms"])
        + [""]
    )
    ok, n, msg = audit_mod.verify(run_dir / "audit.jsonl")
    out += [f"Audit chain: **{'verified' if ok else 'BROKEN'}** ({msg}).", ""]
    diffs = sorted(p.name for p in (run_dir / "changes").glob("*.diff"))
    out += [
        "## 14. Change sets",
        "",
        "Every applied change (including rolled-back attempts) is kept in `changes/`:",
        "",
    ]
    out += [f"- `changes/{d}`" for d in diffs] + [""]
    path = run_dir / "report.md"
    path.write_text("\n".join(out))
    return path
