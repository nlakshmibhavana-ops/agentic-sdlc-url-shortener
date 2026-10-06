"""Playbooks: reviewed change catalogues the deterministic agents draw from.

A playbook is the offline stand-in for model-generated code. The orchestration, policy,
gates, approvals, rollback and re-planning around it are identical in both modes; only
the source of a change set differs (see agents/implement.py and agents/llm.py).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

from ..models import ChangeSet, FileOp

ROOT = Path(__file__).parent


def _flatten(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        out.extend(_flatten(item) if isinstance(item, list) else [item])
    return out


@cache
def load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((ROOT / name / "manifest.yaml").read_text())
    data["dir"] = str(ROOT / name)
    data["tasks_by_id"] = {task["id"]: task for task in data["tasks"]}
    return data


def task(name: str, task_id: str) -> dict[str, Any]:
    return load(name)["tasks_by_id"][task_id]


def changeset(name: str, task_id: str, candidate: int = 0) -> ChangeSet:
    spec = task(name, task_id)
    variants = spec.get("candidates") or [{"summary": spec["title"], "ops": spec["ops"]}]
    candidate = min(candidate, len(variants) - 1)
    variant = variants[candidate]
    base = Path(load(name)["dir"])
    ops = []
    for raw in _flatten(variant["ops"]):
        content = (base / raw["src"]).read_text() if "src" in raw else raw.get("content")
        ops.append(FileOp(raw["op"], raw["path"], content, raw.get("old"), raw.get("new")))
    return ChangeSet(task_id, variant["summary"], ops, candidate)


def candidate_count(name: str, task_id: str) -> int:
    return len(task(name, task_id).get("candidates") or [None])
