"""Core data types shared by the engine, agents and gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class NodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    WAITING = "waiting"  # needs a human (approval or clarification)
    BLOCKED = "blocked"  # an upstream node failed or was rejected
    SKIPPED = "skipped"  # removed by a re-plan
    STOPPED = "stopped"  # interrupted by safe-stop; resumable


TERMINAL = {NodeStatus.SUCCEEDED, NodeStatus.FAILED, NodeStatus.BLOCKED, NodeStatus.SKIPPED}


class Impact(StrEnum):
    LOW = "low"
    HIGH = "high"  # requires human approval before the change is applied


def stable_hash(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class Node:
    """One unit of work in the delivery graph.

    Stage nodes (requirements, design, plan, test, review, docs, release) are created by
    the scenario; task nodes (implementation work) are created and revised by the planner.
    """

    id: str
    kind: str
    agent: str
    title: str
    deps: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)  # artifacts read
    outputs: list[str] = field(default_factory=list)  # artifacts written
    entry_gates: list[str] = field(default_factory=list)
    exit_gates: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    writes: list[str] = field(default_factory=list)  # workspace paths this node may change
    impact: Impact = Impact.LOW
    max_attempts: int = 2
    fallback_agent: str | None = None
    critical: bool = True
    origin: str = "scenario"  # "scenario" or "plan:v<n>"
    # runtime state
    status: NodeStatus = NodeStatus.PENDING
    attempts: int = 0
    fingerprint: str | None = None  # inputs+params hash of the last successful run
    produced: dict[str, int] = field(default_factory=dict)  # artifact -> version produced
    consumed: dict[str, int] = field(default_factory=dict)  # artifact -> version consumed
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    wait_reason: str | None = None
    runs: int = 0  # successful executions (re-runs after invalidation increment this)
    pending: dict[str, Any] | None = None  # a verified change awaiting human approval

    def spec_hash(self) -> str:
        return stable_hash(
            {
                "agent": self.agent,
                "params": self.params,
                "writes": self.writes,
                "exit_gates": self.exit_gates,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Node:
        data = dict(data)
        data["status"] = NodeStatus(data["status"])
        data["impact"] = Impact(data["impact"])
        return cls(**data)


@dataclass
class FileOp:
    op: str  # write | edit | delete
    path: str
    content: str | None = None
    old: str | None = None
    new: str | None = None


@dataclass
class ChangeSet:
    task_id: str
    summary: str
    ops: list[FileOp]
    candidate: int = 0

    def digest(self) -> str:
        return stable_hash([asdict(op) for op in self.ops])

    def paths(self) -> list[str]:
        return sorted({op.path for op in self.ops})


@dataclass
class Decision:
    title: str
    rationale: str
    alternatives: list[str] = field(default_factory=list)
    decided_by: str = "agent"
    node: str = ""
    id: str = ""
    ts: float = 0.0
    inputs: dict[str, int] = field(default_factory=dict)


@dataclass
class AgentResult:
    artifacts: dict[str, Any] = field(default_factory=dict)
    changeset: ChangeSet | None = None
    decisions: list[Decision] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tokens: int = 0


@dataclass
class GateResult:
    gate: str
    passed: bool
    detail: str = ""
    human: bool = False  # failing means "ask a human", not "the work is wrong"


class AgentError(Exception):
    """Raised by an agent when it cannot produce a usable result (retryable)."""
