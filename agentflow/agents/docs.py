"""Documentation: OpenAPI contract, API reference, design record and changelog,
generated from the running code and the run's own artifacts (so docs cannot drift)."""

from __future__ import annotations

import json
from pathlib import Path

from .. import playbooks
from ..models import AgentError, AgentResult, ChangeSet, FileOp
from ..runner import arun_python
from .base import Agent, AgentContext

OPENAPI_SCRIPT = """
import importlib, json, sys
module, attr = sys.argv[1].split(":")
app = getattr(importlib.import_module(module), attr)()
print(json.dumps(app.openapi(), indent=2, sort_keys=True))
"""


def render_api_md(spec: dict) -> str:
    lines = [
        f"# {spec['info']['title']} API (v{spec['info']['version']})",
        "",
        "Generated from the application's OpenAPI schema (`openapi.json`).",
        "",
        "| Method | Path | Summary | Responses |",
        "|---|---|---|---|",
    ]
    for path, methods in sorted(spec["paths"].items()):
        for method, op in methods.items():
            codes = ", ".join(sorted(op.get("responses", {})))
            lines.append(f"| {method.upper()} | `{path}` | {op.get('summary', '')} | {codes} |")
    return "\n".join(lines) + "\n"


def render_design_md(design: dict, req: dict) -> str:
    out = ["# Design record", "", f"Architecture: {design['style']}", "", "## Capabilities", ""]
    for cap in req["capabilities"]:
        out.append(f"- **{cap['id']}**: " + "; ".join(cap["acceptance_criteria"]))
    out += ["", "## Decisions (ADRs)", ""]
    for adr in design["adrs"]:
        out += [
            f"### {adr['id']}: {adr['title']}",
            "",
            adr["rationale"],
            "",
            "Alternatives considered: " + "; ".join(adr["alternatives"]),
            "",
        ]
    if req.get("assumptions"):
        out += ["## Assumptions", ""] + [f"- {a}" for a in req["assumptions"]] + [""]
    if req.get("out_of_scope"):
        out += ["## Out of scope", ""] + [f"- {o}" for o in req["out_of_scope"]] + [""]
    return "\n".join(out)


class DocsAgent(Agent):
    name = "docs"

    async def run(self, ctx: AgentContext) -> AgentResult:
        book = playbooks.load(ctx.playbook)
        cfg = book.get("docs", {})
        ws = ctx.workspace
        async with ctx.lock:
            result = await arun_python(
                ["-c", OPENAPI_SCRIPT, cfg["openapi_factory"]], ws.root, env=cfg.get("env", {})
            )
        if not result.ok:
            raise AgentError(f"could not build OpenAPI schema: {result.tail(5)}")
        spec = json.loads(result.stdout)
        plan = ctx.store.get("plan")
        req = ctx.store.get("requirements")
        entry = [f"## [Unreleased] - {ctx.scenario['title']}", ""]
        entry += [f"- {t['title']}" for t in plan["tasks"] if t["agent"] != "none"]
        changelog = "\n".join(["# Changelog", "", *entry, ""])
        if ws.exists("CHANGELOG.md"):
            changelog = "\n".join(
                [
                    "# Changelog",
                    "",
                    *entry,
                    "",
                    ws.read("CHANGELOG.md").removeprefix("# Changelog\n").lstrip(),
                ]
            )
        ops = [
            FileOp("write", "openapi.json", json.dumps(spec, indent=2, sort_keys=True) + "\n"),
            FileOp("write", "docs/API.md", render_api_md(spec)),
            FileOp("write", "docs/DESIGN.md", render_design_md(ctx.store.get("design"), req)),
            FileOp("write", "CHANGELOG.md", changelog),
        ]
        if cfg.get("readme") and not ws.exists("README.md"):
            ops.append(FileOp("write", "README.md", (Path(book["dir"]) / cfg["readme"]).read_text()))
        endpoints = sorted(f"{m.upper()} {p}" for p, ms in spec["paths"].items() for m in ms)
        return AgentResult(
            artifacts={"docs": {"files": [op.path for op in ops], "endpoints": endpoints}},
            changeset=ChangeSet(ctx.node.id, "Generate API reference, design record, changelog", ops),
        )
