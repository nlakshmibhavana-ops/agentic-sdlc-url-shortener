"""Codebase reasoning for brownfield work: static analysis of the existing system to find
the modules, routes, tables and tests a requirement touches, plus latent defects."""

from __future__ import annotations

import ast
import re
from typing import Any

from ..models import AgentResult, Decision
from ..workspace import Workspace
from .base import Agent, AgentContext

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
TABLE_RE = re.compile(r"(?i)CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)")

# Which symbols a capability is likely to touch (matched against names, routes and SQL).
CAPABILITY_HINTS = {
    "url_safety": ["url", "shorten", "validate"],
    "expiry": ["urls", "create", "lookup", "go", "info", "init_db", "expires"],
    "click_accuracy": ["hit", "clicks", "go"],
    "analytics": ["click", "stats", "info"],
    "redirect": ["follow", "resolve", "redirect", "go"],
    "link_creation": ["create", "shorten", "generate_code"],
    "redirect_cache": ["resolve", "follow"],
    "domain_denylist": ["validate_target_url", "validate"],
    "unique_visitors": ["record_click", "stats", "clicks"],
    "unique_visitors_raw": ["record_click", "clicks"],
}


def _module_name(path: str) -> str:
    return path[:-3].replace("/", ".").removesuffix(".__init__")


def analyse(ws: Workspace) -> dict[str, Any]:
    modules: dict[str, dict[str, Any]] = {}
    tests: dict[str, list[str]] = {}
    for path in ws.files(".py"):
        tree = ast.parse(ws.read(path))
        name = _module_name(path)
        info: dict[str, Any] = {
            "path": path,
            "functions": [],
            "imports": [],
            "routes": [],
            "tables": [],
            "smells": [],
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:  # relative import
                    pkg = (
                        name.split(".")[: -node.level]
                        if not path.endswith("__init__.py")
                        else name.split(".")
                    )
                    base = ".".join([*pkg, base] if base else pkg)
                info["imports"].append(base)
                info["imports"] += [f"{base}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                info["imports"] += [a.name for a in node.names]
            elif isinstance(node, ast.FunctionDef):
                info["functions"].append(node.name)
                for deco in node.decorator_list:
                    if (
                        isinstance(deco, ast.Call)
                        and isinstance(deco.func, ast.Attribute)
                        and deco.func.attr in HTTP_METHODS
                        and deco.args
                        and isinstance(deco.args[0], ast.Constant)
                    ):
                        info["routes"].append(
                            {
                                "method": deco.func.attr.upper(),
                                "path": deco.args[0].value,
                                "handler": node.name,
                            }
                        )
                info["smells"] += _smells(node)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                info["tables"] += TABLE_RE.findall(node.value)
        if path.startswith("tests/") or "/tests/" in path:
            tests[path] = info["imports"]
        else:
            modules[name] = info

    internal = set(modules)
    graph = {
        m: sorted({i for i in info["imports"] if i in internal and i != m}) for m, info in modules.items()
    }
    # A test reaches code through its own imports and through conftest fixtures in its dir.
    conftests = {t.rsplit("/", 1)[0]: imps for t, imps in tests.items() if t.endswith("conftest.py")}
    test_map = {
        t: sorted({i for i in [*imps, *conftests.get(t.rsplit("/", 1)[0], [])] if i in internal})
        for t, imps in tests.items()
        if not t.endswith(("conftest.py", "__init__.py"))
    }
    return {"modules": modules, "import_graph": graph, "tests": test_map}


def _own_nodes(fn: ast.FunctionDef) -> list[ast.AST]:
    """Nodes of fn's body, excluding nested functions (they are analysed on their own)."""
    out, stack = [], list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _smells(fn: ast.FunctionDef) -> list[dict[str, str]]:
    """Heuristics for defects reviewers look for. Reported as evidence, not auto-fixed."""
    found: list[dict[str, str]] = []
    nodes = _own_nodes(fn)
    src_strings = [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    selects = [s for s in src_strings if s.lstrip().upper().startswith("SELECT")]
    updates = [s for s in src_strings if re.search(r"(?i)UPDATE \w+ SET (\w+) = \?", s)]
    for upd in updates:
        col = re.search(r"(?i)SET (\w+) = \?", upd).group(1)
        if any(col in s for s in selects):
            found.append(
                {
                    "function": fn.name,
                    "kind": "lost-update",
                    "detail": f"reads then writes '{col}' non-atomically",
                }
            )
    for node in nodes:
        if (
            isinstance(node, ast.keyword)
            and node.arg == "status_code"
            and getattr(node.value, "value", None) == 301
        ):
            found.append(
                {
                    "function": fn.name,
                    "kind": "cacheable-redirect",
                    "detail": "301 is cached by browsers; repeat clicks never reach the server",
                }
            )
        if isinstance(node, ast.Attribute) and node.attr == "lastrowid":
            found.append(
                {
                    "function": fn.name,
                    "kind": "enumerable-ids",
                    "detail": "codes derived from sequential row ids can be enumerated",
                }
            )
    unique = {(f["function"], f["kind"]): f for f in found}
    return list(unique.values())


def reverse_closure(graph: dict[str, list[str]], seeds: set[str]) -> set[str]:
    """Every module that (transitively) imports one of the seeds."""
    out = set(seeds)
    changed = True
    while changed:
        changed = False
        for mod, deps in graph.items():
            if mod not in out and out & set(deps):
                out.add(mod)
                changed = True
    return out


class CodebaseAnalystAgent(Agent):
    name = "codebase"

    async def run(self, ctx: AgentContext) -> AgentResult:
        analysis = analyse(ctx.workspace)
        baseline = ctx.extras.get("baseline_files") or set()
        if baseline:  # regression tests = the existing system's tests, not ones this run added
            analysis["tests"] = {t: m for t, m in analysis["tests"].items() if t in baseline}
        req = ctx.store.get("requirements")
        caps = [c["id"] for c in req["capabilities"]]
        impacted: dict[str, Any] = {}
        all_seeds: set[str] = set()
        for cap in caps:
            hints = CAPABILITY_HINTS.get(cap, [cap])
            seeds = {
                m
                for m, info in analysis["modules"].items()
                if any(
                    h
                    in " ".join(
                        info["functions"] + info["tables"] + [r["path"] for r in info["routes"]]
                    ).lower()
                    for h in hints
                )
            }
            all_seeds |= seeds
            impacted[cap] = {
                "modules": sorted(seeds),
                "routes": sorted(
                    {
                        f"{r['method']} {r['path']}"
                        for m in seeds
                        for r in analysis["modules"][m]["routes"]
                        if any(h in (r["handler"] + r["path"]).lower() for h in hints)
                    }
                ),
                "tables": sorted({t for m in seeds for t in analysis["modules"][m]["tables"]}),
            }
        blast = reverse_closure(analysis["import_graph"], all_seeds)
        regression = sorted(t for t, mods in analysis["tests"].items() if set(mods) & blast)
        smells = [dict(s, module=m) for m, info in analysis["modules"].items() for s in info["smells"]]
        impact = {
            "modules": {
                m: {k: v for k, v in i.items() if k != "smells"} for m, i in analysis["modules"].items()
            },
            "import_graph": analysis["import_graph"],
            "impacted": impacted,
            "blast_radius": sorted(blast),
            "regression_tests": regression,
            "test_map": analysis["tests"],
            "findings": smells,
            "data_at_risk": sorted({t for c in impacted.values() for t in c["tables"]}),
        }
        decisions = []
        if any(s["kind"] == "enumerable-ids" for s in smells):
            decisions.append(
                Decision(
                    title="Sequential/enumerable codes noted but left out of scope",
                    rationale="Changing code generation alters every future URL format and is not "
                    "requested by LINK-142/150/151; raised as a follow-up risk instead.",
                    alternatives=["Switch to random codes now (scope creep, needs product sign-off)"],
                )
            )
        return AgentResult(
            artifacts={"impact_analysis": impact},
            decisions=decisions,
            notes=[
                f"blast radius {impact['blast_radius']}, {len(smells)} latent defects, "
                f"regression tests {regression}"
            ],
        )
