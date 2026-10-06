"""Implementation agents: produce a change set for one planned task.

ImplementAgent draws from the reviewed playbook (deterministic, reproducible demos).
LLMImplementAgent asks Claude for the change, feeding back the previous attempt's gate
failures. Either way the engine policy-checks, applies, verifies and, if needed, rolls
back the change; an agent never writes to the workspace directly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .. import playbooks
from ..models import AgentError, AgentResult, ChangeSet, FileOp
from .base import Agent, AgentContext


class ImplementAgent(Agent):
    name = "implement"

    async def run(self, ctx: AgentContext) -> AgentResult:
        task_id = ctx.node.params["task"]
        total = playbooks.candidate_count(ctx.playbook, task_id)
        if total > 1 and ctx.attempt > total:
            raise AgentError(f"all {total} candidate implementations exhausted")
        cs = playbooks.changeset(ctx.playbook, task_id, ctx.attempt - 1)
        note = f"candidate {cs.candidate + 1}/{total}: {cs.summary}"
        if ctx.extras.get("feedback"):
            note += f" (previous attempt failed: {ctx.extras['feedback'][:120]})"
        return AgentResult(changeset=cs, notes=[note])


class _File(BaseModel):
    path: str = Field(description="Workspace-relative path; must be one of the allowed paths")
    content: str = Field(description="Complete new file content")


class _Change(BaseModel):
    summary: str
    files: list[_File]


class LLMImplementAgent(Agent):
    name = "implement.llm"

    async def run(self, ctx: AgentContext) -> AgentResult:
        if ctx.llm is None:
            raise AgentError("no LLM provider configured")
        node = ctx.node
        task = node.params
        current = {p: ctx.workspace.read(p) for p in node.writes if ctx.workspace.exists(p)}
        tests = {
            p: ctx.workspace.read(p)
            for p in task.get("verify", [])
            if p.endswith(".py") and ctx.workspace.exists(p)
        }
        design = ctx.store.get("design", {})
        prompt = "\n\n".join(
            [
                f"Task {task['task']}: {node.title}",
                f"Acceptance tests that must pass:\n{tests or '(none yet)'}",
                f"Allowed paths (write only these): {node.writes}",
                f"Current content of those paths:\n{current or '(new files)'}",
                f"Design decisions:\n{design.get('adrs', [])}",
                f"Previous attempt feedback:\n{ctx.extras.get('feedback', 'none')}",
            ]
        )
        change, tokens = await ctx.llm.structured(
            system="You are a senior Python engineer. Produce minimal, production-quality changes "
            "that make the acceptance tests pass. Return complete file contents. Never "
            "add dependencies, secrets, eval/exec or shell calls.",
            prompt=prompt,
            schema=_Change,
        )
        if not change.files:
            raise AgentError("model returned no files")
        ops = [FileOp("write", f.path, f.content) for f in change.files]
        return AgentResult(
            changeset=ChangeSet(task["task"], change.summary, ops),
            tokens=tokens,
            notes=[f"model change: {change.summary}"],
        )
