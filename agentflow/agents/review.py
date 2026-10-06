"""Automated review: lint, security scan and leftover latent defects."""

from __future__ import annotations

import json

from ..models import AgentResult
from ..policy import scan_python, scan_text
from ..runner import arun_python
from .base import Agent, AgentContext
from .codebase import analyse


class ReviewAgent(Agent):
    name = "reviewer"

    async def run(self, ctx: AgentContext) -> AgentResult:
        ws = ctx.workspace
        async with ctx.lock:
            lint = await arun_python(
                ["-m", "ruff", "check", "--no-cache", "--output-format=json", "."], ws.root
            )
            sources = {p: ws.read(p) for p in ws.files() if p.endswith((".py", ".toml", ".md", ".yaml"))}
            smells = analyse(ws)
        issues = json.loads(lint.stdout or "[]")
        security = []
        for path, text in sources.items():
            if path.endswith(".py") and not path.startswith("tests/"):
                security += scan_python(path, text)
            security += scan_text(path, text.splitlines())
        leftovers = [dict(s, module=m) for m, info in smells["modules"].items() for s in info["smells"]]
        report = {
            "lint": [
                f"{i['filename'].replace(str(ws.root) + '/', '')}:{i['location']['row']} "
                f"{i['code']} {i['message']}"
                for i in issues
            ],
            "security": [vars(f) for f in security],
            "latent_defects": leftovers,
        }
        return AgentResult(
            artifacts={"review_report": report},
            notes=[
                f"{len(report['lint'])} lint, {len(security)} security, "
                f"{len(leftovers)} latent defects (reported, non-blocking)"
            ],
        )
