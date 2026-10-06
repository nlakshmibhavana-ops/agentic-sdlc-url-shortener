"""The workspace an agent changes: path confinement, change application and undo logs."""

from __future__ import annotations

import difflib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .models import ChangeSet

IGNORED = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".coverage"}


class WorkspaceError(Exception):
    pass


@dataclass
class UndoLog:
    """Previous content of every path a change set touched (None = did not exist)."""

    before: dict[str, str | None] = field(default_factory=dict)


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if path != self.root and self.root not in path.parents:
            raise WorkspaceError(f"path escapes workspace: {rel}")
        return path

    def exists(self, rel: str) -> bool:
        return self.resolve(rel).exists()

    def read(self, rel: str) -> str:
        return self.resolve(rel).read_text()

    def files(self, suffix: str = "") -> list[str]:
        out = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and not IGNORED & set(path.relative_to(self.root).parts):
                rel = str(path.relative_to(self.root))
                if rel.endswith(suffix):
                    out.append(rel)
        return out

    def seed(self, source: Path) -> None:
        shutil.copytree(source, self.root, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*IGNORED))

    def preview(self, changeset: ChangeSet) -> dict[str, tuple[str | None, str | None]]:
        """Compute {path: (before, after)} without touching disk. Raises on a bad anchor."""
        state: dict[str, str | None] = {}
        for op in changeset.ops:
            path = self.resolve(op.path)
            if op.path not in state:
                state[op.path] = path.read_text() if path.exists() else None
        result = {p: (before, before) for p, before in state.items()}
        current = dict(state)
        for op in changeset.ops:
            text = current[op.path]
            if op.op == "write":
                current[op.path] = op.content or ""
            elif op.op == "delete":
                current[op.path] = None
            elif op.op == "edit":
                if text is None:
                    raise WorkspaceError(f"edit of missing file {op.path}")
                count = text.count(op.old or "")
                if not op.old or count != 1:
                    raise WorkspaceError(f"edit anchor in {op.path} matched {count} times (needs exactly 1)")
                current[op.path] = text.replace(op.old, op.new or "", 1)
            else:
                raise WorkspaceError(f"unknown op {op.op}")
        return {p: (result[p][0], current[p]) for p in current}

    def apply(self, changeset: ChangeSet) -> tuple[UndoLog, str]:
        """Apply atomically (all edits validated first). Returns the undo log and a diff."""
        planned = self.preview(changeset)
        undo = UndoLog({p: before for p, (before, _) in planned.items()})
        for rel, (_, after) in planned.items():
            path = self.resolve(rel)
            if after is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(after)
        return undo, unified_diff(planned)

    def rollback(self, undo: UndoLog) -> None:
        for rel, before in undo.before.items():
            path = self.resolve(rel)
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(before)


def unified_diff(planned: dict[str, tuple[str | None, str | None]]) -> str:
    chunks = []
    for rel, (before, after) in sorted(planned.items()):
        chunks.extend(
            difflib.unified_diff(
                (before or "").splitlines(keepends=True),
                (after or "").splitlines(keepends=True),
                fromfile=f"a/{rel}" if before is not None else "/dev/null",
                tofile=f"b/{rel}" if after is not None else "/dev/null",
            )
        )
    return "".join(chunks)


def diff_stats(diff: str) -> tuple[int, int]:
    added = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return added, removed
