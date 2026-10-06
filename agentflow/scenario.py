"""Scenario loading and the SDLC stage templates.

The stage skeleton is fixed per pipeline; implementation tasks are inserted by the
planner at run time. Join stages (test, review, docs) automatically depend on every task
in the current plan, so they act as synchronisation barriers after the parallel work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import Impact, Node

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios"


def load(name_or_path: str) -> dict[str, Any]:
    path = Path(name_or_path)
    if not path.exists():
        path = SCENARIOS / f"{name_or_path}.yaml"
    data = yaml.safe_load(path.read_text())
    data.setdefault("pipeline", "greenfield")
    data.setdefault("policy", {})
    data.setdefault("budgets", {"max_attempts": 80, "max_active_s": 1800})
    return data


def stage_nodes(scenario: dict[str, Any]) -> list[Node]:
    llm = scenario.get("provider") in ("claude", "ollama")
    brownfield = scenario["pipeline"] == "brownfield"
    nodes = [
        Node(
            "requirements",
            "stage",
            "requirements.llm" if llm else "requirements",
            "Understand and normalise the requirement; surface ambiguity",
            inputs=["requirement_text", "human_answers"],
            outputs=["requirements"],
            exit_gates=["requirements_valid"],
            fallback_agent="requirements" if llm else None,
        ),
    ]
    design_deps = ["requirements"]
    design_inputs = ["requirements"]
    if brownfield:
        nodes.append(
            Node(
                "analyze",
                "stage",
                "codebase",
                "Analyse the existing codebase: impacted modules, APIs, data, tests",
                deps=["requirements"],
                inputs=["requirements"],
                outputs=["impact_analysis"],
            )
        )
        design_deps, design_inputs = ["analyze"], ["requirements", "impact_analysis"]
    nodes += [
        Node(
            "design",
            "stage",
            "design",
            "Architecture and design decisions (ADRs)",
            deps=design_deps,
            inputs=design_inputs,
            outputs=["design"],
            exit_gates=["design_covers_requirements"],
        ),
        Node(
            "plan",
            "stage",
            "planner",
            "Decompose into a dependency-ordered task graph",
            deps=["design"],
            inputs=[*design_inputs, "design"],
            outputs=["plan"],
            exit_gates=["plan_valid"],
        ),
        Node(
            "test",
            "stage",
            "test_runner",
            "Run the full test suite with coverage",
            deps=["plan"],
            outputs=["test_report"],
            exit_gates=["tests_pass", "coverage_min"],
            params={"join": True},
        ),
        Node(
            "review",
            "stage",
            "reviewer",
            "Automated review: lint, security, latent defects",
            deps=["plan"],
            outputs=["review_report"],
            exit_gates=["lint_clean", "security_clean"],
            params={"join": True},
        ),
        Node(
            "docs",
            "stage",
            "docs",
            "Generate OpenAPI, API reference, design record, changelog",
            deps=["plan"],
            inputs=["requirements", "design", "plan"],
            outputs=["docs"],
            writes=["openapi.json", "docs/*", "CHANGELOG.md", "README.md"],
            exit_gates=["openapi_valid", "docs_complete"],
            params={"join": True},
        ),
        Node(
            "readiness",
            "stage",
            "release_readiness",
            "Collect release evidence and residual risks",
            deps=["test", "review", "docs"],
            inputs=["test_report", "review_report", "docs", "requirements"],
            outputs=["release_readiness"],
            exit_gates=["readiness"],
        ),
        Node(
            "release",
            "stage",
            "release",
            "Cut the release (version, notes) after human approval",
            deps=["readiness"],
            inputs=["release_readiness", "plan"],
            outputs=["release"],
            writes=["VERSION", "RELEASE_NOTES.md", "pyproject.toml"],
            impact=Impact.HIGH,
            exit_gates=["smoke"],
            max_attempts=1,
        ),
    ]
    return nodes


def task_node(task: dict[str, Any], plan_version: int) -> Node:
    if task["agent"] == "none":
        return Node(
            task["id"],
            "checkpoint",
            "none",
            task["title"],
            deps=["plan"],
            entry_gates=[task["gate"]],
            origin=f"plan:v{plan_version}",
        )
    gates = ["compiles", "lint"] + (["tests"] if task["verify"] else [])
    return Node(
        task["id"],
        "task",
        task["agent"],
        task["title"],
        deps=["plan", *task["deps"]],
        exit_gates=gates,
        params={"task": task["id"], "verify": task["verify"], "candidates": task["candidates"]},
        writes=task["writes"],
        impact=Impact(task["impact"]),
        max_attempts=max(2, task["candidates"]),
        fallback_agent="implement" if task["agent"] == "implement.llm" else None,
        origin=f"plan:v{plan_version}",
    )
