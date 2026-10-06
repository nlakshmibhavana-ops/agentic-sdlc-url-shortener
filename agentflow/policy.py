"""Policy guardrails evaluated on every change set *before* it touches the workspace.

Severity:
  block    - the change is refused (counts as a failed attempt; the agent may retry)
  approve  - allowed only with a recorded human approval bound to the change's digest
  warn     - recorded in the audit trail and report
"""

from __future__ import annotations

import ast
import fnmatch
import re
import tomllib
from dataclasses import dataclass, field
from typing import Any

from .models import ChangeSet, Node

DEFAULTS: dict[str, Any] = {
    "forbidden_paths": [".env", ".env.*", "*.pem", "*.key", ".git/*", "secrets/*"],
    "protected_paths": ["pyproject.toml", ".github/*", "deploy/*", "Dockerfile"],
    "max_changed_lines": 600,
    # Machine-generated files are verified by gates, not read line by line, so they do not
    # count toward the human-review diff budget.
    "generated_paths": ["openapi.json"],
    "dependency_allowlist": [
        "fastapi",
        "uvicorn",
        "pydantic",
        "httpx",
        "pytest",
        "pytest-cov",
        "ruff",
    ],
}

SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "Anthropic API key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "GitHub token"),
    (
        re.compile(r"""(?i)\b(password|passwd|secret|api_key|token)\s*=\s*["'][^"'\s]{8,}["']"""),
        "hard-coded credential",
    ),
]
SCHEMA_RE = re.compile(r"(?i)\b(CREATE|ALTER|DROP)\s+TABLE\b|\bMIGRATIONS\b")
PII_COLUMN_RE = re.compile(r"(?i)\b(ip|ip_address|remote_addr|client_ip|email)\s+(TEXT|VARCHAR)")


@dataclass
class Finding:
    rule: str
    severity: str
    path: str
    message: str


@dataclass
class PolicyResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "block"]

    @property
    def approvals(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "approve"]


def _match(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


def _deps(pyproject: str | None) -> set[str]:
    if not pyproject:
        return set()
    data = tomllib.loads(pyproject).get("project", {})
    reqs = list(data.get("dependencies", []))
    for extra in data.get("optional-dependencies", {}).values():
        reqs += extra
    return {re.split(r"[<>=!~\[ ;]", r, maxsplit=1)[0].strip().lower() for r in reqs}


def _added_lines(before: str | None, after: str | None) -> list[str]:
    old = set((before or "").splitlines())
    return [line for line in (after or "").splitlines() if line not in old]


def _ddl(source: str | None) -> set[str]:
    """SQL DDL string literals in a Python file (implicit concatenation is one literal)."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return set()
    return {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and SCHEMA_RE.search(n.value)
    }


def _schema_changed(path: str, before: str | None, after: str | None, changed: list[str]) -> bool:
    if path.endswith(".py"):
        return _ddl(before) != _ddl(after)
    return any(SCHEMA_RE.search(line) for line in changed)


class _DangerVisitor(ast.NodeVisitor):
    BANNED = {"eval", "exec", "compile", "__import__"}
    BANNED_ATTRS = {("os", "system"), ("pickle", "loads"), ("pickle", "load"), ("marshal", "loads")}

    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in self.BANNED:
            self.hits.append((node.lineno, f"call to {func.id}()"))
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            pair = (func.value.id, func.attr)
            if pair in self.BANNED_ATTRS:
                self.hits.append((node.lineno, f"call to {pair[0]}.{pair[1]}()"))
            if pair == ("yaml", "load") and not any(k.arg == "Loader" for k in node.keywords):
                self.hits.append((node.lineno, "yaml.load without an explicit safe Loader"))
        for kw in node.keywords:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                self.hits.append((node.lineno, "subprocess call with shell=True"))
        if isinstance(func, ast.Attribute) and func.attr in {"execute", "executemany"} and node.args:
            query = node.args[0]
            dynamic = (
                isinstance(query, ast.JoinedStr)
                or (isinstance(query, ast.BinOp) and isinstance(query.op, (ast.Add, ast.Mod)))
                or (
                    isinstance(query, ast.Call)
                    and isinstance(query.func, ast.Attribute)
                    and query.func.attr == "format"
                )
            )
            if dynamic:
                self.hits.append((node.lineno, "SQL built from string formatting (injection risk)"))
        self.generic_visit(node)


def scan_python(path: str, source: str) -> list[Finding]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Finding("SEC-000", "block", path, f"does not parse: {exc.msg} (line {exc.lineno})")]
    visitor = _DangerVisitor()
    visitor.visit(tree)
    return [Finding("SEC-002", "block", path, f"line {ln}: {msg}") for ln, msg in visitor.hits]


# Values that are obviously fixtures, not credentials (the usual scanner allowlist).
PLACEHOLDER_RE = re.compile(r"(?i)test|example|dummy|fake|placeholder|change-?me|smoke|xxx")


def scan_text(path: str, lines: list[str]) -> list[Finding]:
    findings = []
    for line in lines:
        for pattern, label in SECRET_PATTERNS:
            match = pattern.search(line)
            if match and label == "hard-coded credential" and PLACEHOLDER_RE.search(match.group(0)):
                continue
            if match:
                findings.append(Finding("SEC-001", "block", path, f"possible {label} committed"))
    return findings


class PolicyEngine:
    def __init__(self, config: dict[str, Any] | None = None, baseline: set[str] | None = None):
        self.config = {**DEFAULTS, **(config or {})}
        self.baseline = baseline or set()  # files that existed before the run (live system)

    def evaluate(
        self, node: Node, changeset: ChangeSet, planned: dict[str, tuple[str | None, str | None]]
    ) -> PolicyResult:
        result = PolicyResult()
        add = result.findings.append
        changed_lines = 0
        for path, (before, after) in sorted(planned.items()):
            added = _added_lines(before, after)
            removed = _added_lines(after, before)
            if not _match(path, self.config["generated_paths"]):
                changed_lines += len(added) + len(removed)

            if _match(path, self.config["forbidden_paths"]):
                add(Finding("CHG-001", "block", path, "path is forbidden for agents"))
            if node.writes and not any(fnmatch.fnmatch(path, w) or path == w for w in node.writes):
                add(Finding("CHG-003", "block", path, f"outside the task's declared write set {node.writes}"))
            if before is not None and _match(path, self.config["protected_paths"]):
                add(Finding("CHG-002", "approve", path, "modifies a protected file"))
            if path in self.baseline and _schema_changed(path, before, after, added + removed):
                add(Finding("DATA-001", "approve", path, "schema change against an existing database"))
            if any(PII_COLUMN_RE.search(line) for line in added):
                add(Finding("PII-001", "block", path, "persists a raw personal identifier (IP/email)"))
            if path.endswith(".py") and after is not None:
                if "client.host" in after and "hmac" not in after and "hash" not in after:
                    add(Finding("PII-002", "block", path, "client IP used without hashing"))
                result.findings += scan_python(path, after)
            result.findings += scan_text(path, added)
            if path.endswith("pyproject.toml"):
                new_deps = _deps(after) - _deps(before)
                allow = set(self.config["dependency_allowlist"])
                for dep in sorted(new_deps):
                    if dep not in allow:
                        add(Finding("DEP-001", "block", path, f"dependency '{dep}' is not on the allowlist"))
                    elif before is not None:
                        add(Finding("DEP-002", "approve", path, f"adds dependency '{dep}'"))

        if changed_lines > self.config["max_changed_lines"]:
            add(
                Finding(
                    "CHG-004",
                    "approve",
                    "*",
                    f"{changed_lines} changed lines exceeds budget of {self.config['max_changed_lines']}",
                )
            )
        return result
