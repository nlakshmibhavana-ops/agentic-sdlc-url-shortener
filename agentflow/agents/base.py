from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..context import ContextStore
from ..models import AgentResult, Node
from ..workspace import Workspace


@dataclass
class AgentContext:
    node: Node
    store: ContextStore
    workspace: Workspace
    scenario: dict[str, Any]
    attempt: int
    run_dir: Path
    lock: asyncio.Lock
    llm: Any = None  # agents.llm.ClaudeClient when provider == "claude"
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def playbook(self) -> str:
        return self.scenario["playbook"]


class Agent:
    name = "agent"

    async def run(self, ctx: AgentContext) -> AgentResult:  # pragma: no cover - interface
        raise NotImplementedError
