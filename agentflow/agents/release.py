"""Release readiness (evidence gathering) and release (version + notes, human-approved)."""

from __future__ import annotations

import re
import tomllib

from .. import playbooks
from ..models import AgentResult, ChangeSet, FileOp
from .base import Agent, AgentContext


class ReleaseReadinessAgent(Agent):
    name = "release_readiness"

    async def run(self, ctx: AgentContext) -> AgentResult:
        s = ctx.store
        tests, review, docs, req = (
            s.get("test_report"),
            s.get("review_report"),
            s.get("docs"),
            s.get("requirements"),
        )
        cov_min = playbooks.load(ctx.playbook).get("coverage_min", 80)
        checks = [
            (
                "all tests pass",
                tests["ok"] and tests["failed"] == 0,
                f"{tests['passed']} passed / {tests['failed']} failed",
            ),
            (f"coverage >= {cov_min}%", (tests["coverage"] or 0) >= cov_min, f"{tests['coverage']}%"),
            ("lint clean", not review["lint"], f"{len(review['lint'])} issues"),
            ("no security findings", not review["security"], f"{len(review['security'])} findings"),
            ("docs generated", bool(docs and docs["files"]), ", ".join(docs["files"]) if docs else ""),
            ("no blocking questions open", not req["blocking_open"], str(req["blocking_open"])),
        ]
        risks = [f"{d['module']}.{d['function']}: {d['detail']}" for d in review["latent_defects"]]
        risks += [f"assumption: {a}" for a in req.get("assumptions", [])]
        readiness = {
            "checks": [{"check": c, "passed": bool(p), "detail": d} for c, p, d in checks],
            "ready": all(p for _, p, _ in checks),
            "residual_risks": risks,
        }
        return AgentResult(
            artifacts={"release_readiness": readiness},
            notes=[f"ready={readiness['ready']}, {len(risks)} residual risks"],
        )


def bump(version: str, minor: bool) -> str:
    major, mnr, patch = (int(x) for x in re.findall(r"\d+", version)[:3])
    return f"{major}.{mnr + 1}.0" if minor else f"{major}.{mnr}.{patch + 1}"


class ReleaseAgent(Agent):
    name = "release"

    async def run(self, ctx: AgentContext) -> AgentResult:
        ws = ctx.workspace
        pyproject = ws.read("pyproject.toml")
        current = tomllib.loads(pyproject)["project"]["version"]
        baseline = ctx.extras.get("baseline_files", set())
        greenfield = "pyproject.toml" not in baseline
        plan = ctx.store.get("plan")
        readiness = ctx.store.get("release_readiness")
        new = current if greenfield else bump(current, minor=True)
        notes = [f"# Release {new}", "", f"{ctx.scenario['title']}", "", "## Changes", ""]
        notes += [f"- {t['title']}" for t in plan["tasks"] if t["agent"] != "none"]
        notes += ["", "## Evidence", ""]
        notes += [
            f"- [{'x' if c['passed'] else ' '}] {c['check']}: {c['detail']}" for c in readiness["checks"]
        ]
        if readiness["residual_risks"]:
            notes += ["", "## Known risks / follow-ups", ""] + [f"- {r}" for r in readiness["residual_risks"]]
        ops = [
            FileOp("write", "VERSION", new + "\n"),
            FileOp("write", "RELEASE_NOTES.md", "\n".join(notes) + "\n"),
        ]
        if new != current:
            ops.append(
                FileOp("edit", "pyproject.toml", old=f'version = "{current}"', new=f'version = "{new}"')
            )
        return AgentResult(
            artifacts={"release": {"version": new, "previous": current}},
            changeset=ChangeSet(ctx.node.id, f"Release {new}", ops),
        )
