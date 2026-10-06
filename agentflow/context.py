"""Cross-stage context: versioned artifacts with lineage, plus the decision log."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import Decision, stable_hash


@dataclass
class ArtifactVersion:
    name: str
    version: int
    digest: str
    content: Any
    producer: str  # node id, "human:<name>" or "scenario"
    inputs: dict[str, int] = field(default_factory=dict)  # artifact -> version it was derived from
    ts: float = 0.0


class ContextStore:
    """Every value that crosses a stage boundary goes through here, so any output can be
    traced back to the inputs (and humans) that produced it."""

    def __init__(self) -> None:
        self.artifacts: dict[str, list[ArtifactVersion]] = {}
        self.decisions: list[Decision] = []

    def put(
        self, name: str, content: Any, producer: str, inputs: dict[str, int] | None = None
    ) -> tuple[ArtifactVersion, bool]:
        """Store a new version. Returns (version, changed); identical content is not re-versioned."""
        digest = stable_hash(content)
        history = self.artifacts.setdefault(name, [])
        if history and history[-1].digest == digest:
            return history[-1], False
        version = ArtifactVersion(
            name, len(history) + 1, digest, content, producer, dict(inputs or {}), time.time()
        )
        history.append(version)
        return version, True

    def has(self, name: str) -> bool:
        return bool(self.artifacts.get(name))

    def get(self, name: str, default: Any = None) -> Any:
        history = self.artifacts.get(name)
        return history[-1].content if history else default

    def latest(self, name: str) -> ArtifactVersion | None:
        history = self.artifacts.get(name)
        return history[-1] if history else None

    def version(self, name: str) -> int:
        history = self.artifacts.get(name)
        return history[-1].version if history else 0

    def lineage(self, name: str) -> list[dict[str, Any]]:
        """Walk an artifact's provenance back to its roots (latest versions)."""
        seen: set[tuple[str, int]] = set()
        out: list[dict[str, Any]] = []

        def walk(artifact: str, version: int, depth: int) -> None:
            if (artifact, version) in seen or artifact not in self.artifacts:
                return
            seen.add((artifact, version))
            av = self.artifacts[artifact][version - 1]
            out.append({"artifact": artifact, "version": version, "producer": av.producer, "depth": depth})
            for parent, parent_version in sorted(av.inputs.items()):
                walk(parent, parent_version, depth + 1)

        latest = self.latest(name)
        if latest:
            walk(name, latest.version, 0)
        return out

    def add_decision(self, decision: Decision) -> Decision:
        decision.id = decision.id or f"D{len(self.decisions) + 1:03d}"
        decision.ts = decision.ts or time.time()
        self.decisions.append(decision)
        return decision

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifacts": {k: [asdict(v) for v in vs] for k, vs in self.artifacts.items()},
            "decisions": [asdict(d) for d in self.decisions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextStore:
        store = cls()
        store.artifacts = {
            k: [ArtifactVersion(**v) for v in vs] for k, vs in data.get("artifacts", {}).items()
        }
        store.decisions = [Decision(**d) for d in data.get("decisions", [])]
        return store
