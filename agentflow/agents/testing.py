"""Test execution: full suite with coverage, as evidence for the release gate."""

from __future__ import annotations

import json
import re

from .. import playbooks
from ..models import AgentResult
from ..runner import arun_python, pytest_args
from .base import Agent, AgentContext

_COUNTS = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")


def parse_counts(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for n, kind in _COUNTS.findall(output):
        counts["errors" if kind.startswith("error") else kind] += int(n)
    return counts


class TestRunnerAgent(Agent):
    name = "test_runner"

    async def run(self, ctx: AgentContext) -> AgentResult:
        package = playbooks.load(ctx.playbook)["package"]
        cov_json = ctx.run_dir / f"coverage-{ctx.node.id}.json"
        async with ctx.lock:
            result = await arun_python(
                pytest_args(["tests"], package, cov_json),
                ctx.workspace.root,
                timeout=600,
                env={"COVERAGE_FILE": str(ctx.run_dir / ".coverage")},  # keep the product tree clean
            )
        counts = parse_counts(result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "")
        coverage = None
        if cov_json.exists():
            coverage = round(json.loads(cov_json.read_text())["totals"]["percent_covered"], 1)
        report = {
            **counts,
            "total": counts["passed"] + counts["failed"] + counts["errors"],
            "ok": result.ok,
            "coverage": coverage,
            "failures": [line for line in result.stdout.splitlines() if line.startswith("FAILED")],
            "tail": result.tail(),
        }
        return AgentResult(
            artifacts={"test_report": report},
            notes=[f"{counts['passed']} passed, {counts['failed']} failed, coverage {coverage}%"],
        )
