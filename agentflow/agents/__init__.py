"""Agent registry. Each agent owns one SDLC responsibility and returns an AgentResult;
the engine, not the agent, decides whether that result is accepted."""

from __future__ import annotations

from .base import Agent, AgentContext
from .codebase import CodebaseAnalystAgent
from .design import DesignAgent
from .docs import DocsAgent
from .implement import ImplementAgent, LLMImplementAgent
from .planner import PlannerAgent
from .release import ReleaseAgent, ReleaseReadinessAgent
from .requirements import LLMRequirementsAgent, RequirementsAgent
from .review import ReviewAgent
from .testing import TestRunnerAgent

REGISTRY: dict[str, type[Agent]] = {
    "requirements": RequirementsAgent,
    "requirements.llm": LLMRequirementsAgent,
    "codebase": CodebaseAnalystAgent,
    "design": DesignAgent,
    "planner": PlannerAgent,
    "implement": ImplementAgent,
    "implement.llm": LLMImplementAgent,
    "test_author": ImplementAgent,
    "test_runner": TestRunnerAgent,
    "reviewer": ReviewAgent,
    "docs": DocsAgent,
    "release_readiness": ReleaseReadinessAgent,
    "release": ReleaseAgent,
}

__all__ = ["REGISTRY", "Agent", "AgentContext"]
