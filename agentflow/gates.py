"""Entry and exit gates.

A gate spec is "name" or "name:arg". Entry gates decide whether a node may start; exit
gates decide whether its output is accepted. Gates marked human=True do not fail the
work. They park the node until a person acts (answers a question, approves a change).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import playbooks
from .context import ContextStore
from .models import GateResult, Node
from .runner import arun_python, pytest_args
from .workspace import Workspace


@dataclass
class GateContext:
    node: Node
    store: ContextStore
    workspace: Workspace
    scenario: dict[str, Any]
    run_dir: Path

    @property
    def book(self) -> dict[str, Any]:
        return playbooks.load(self.scenario["playbook"])


GateFn = Callable[[GateContext, str], Awaitable[GateResult]]
GATES: dict[str, GateFn] = {}


def gate(name: str):
    def register(fn: GateFn) -> GateFn:
        GATES[name] = fn
        return fn

    return register


async def evaluate(spec: str, gctx: GateContext) -> GateResult:
    name, _, arg = spec.partition(":")
    if name not in GATES:
        return GateResult(spec, False, f"unknown gate '{name}'")
    try:
        result = await GATES[name](gctx, arg)
    except Exception as exc:  # a crashing gate is a failed gate, never a pass
        return GateResult(spec, False, f"gate error: {exc!r}")
    result.gate = spec
    return result


# ---------------------------------------------------------------- entry / human gates


@gate("artifact")
async def _artifact(g: GateContext, name: str) -> GateResult:
    return GateResult(
        "", g.store.has(name), f"artifact '{name}' {'present' if g.store.has(name) else 'missing'}"
    )


@gate("clarified")
async def _clarified(g: GateContext, qid: str) -> GateResult:
    req = g.store.get("requirements", {})
    open_ = qid in req.get("blocking_open", [qid])
    q = next((q for q in req.get("questions", []) if q["id"] == qid), {})
    detail = (
        (f"waiting for a human answer to {qid}: {q.get('text', '')} options={list(q.get('options', {}))}")
        if open_
        else f"{qid} answered"
    )
    return GateResult("", not open_, detail, human=True)


# ---------------------------------------------------------------- artifact quality gates


@gate("requirements_valid")
async def _requirements_valid(g: GateContext, _: str) -> GateResult:
    req = g.store.get("requirements")
    problems = [c["id"] for c in req["capabilities"] if not c["acceptance_criteria"]]
    ok = bool(req["capabilities"]) and not problems
    return GateResult(
        "",
        ok,
        f"{len(req['capabilities'])} capabilities, all with acceptance criteria"
        if ok
        else f"capabilities without acceptance criteria: {problems}",
    )


@gate("design_covers_requirements")
async def _design_covers(g: GateContext, _: str) -> GateResult:
    req, design = g.store.get("requirements"), g.store.get("design")
    missing = [c["id"] for c in req["capabilities"] if c["id"] not in design["components"]]
    return GateResult(
        "",
        not missing,
        "every capability has a design element" if not missing else f"no design for {missing}",
    )


@gate("plan_valid")
async def _plan_valid(g: GateContext, _: str) -> GateResult:
    plan = g.store.get("plan")
    ids = {t["id"] for t in plan["tasks"]}
    bad_deps = [(t["id"], d) for t in plan["tasks"] for d in t["deps"] if d not in ids]
    if bad_deps:
        return GateResult("", False, f"tasks depend on unknown tasks {bad_deps}")
    if plan["uncovered"]:
        return GateResult("", False, f"capabilities without tasks: {plan['uncovered']}")
    return GateResult("", True, f"{len(ids)} tasks; every capability traced to >=1 task")


# ---------------------------------------------------------------- code gates


@gate("compiles")
async def _compiles(g: GateContext, _: str) -> GateResult:
    files = [p for p in g.node.writes if p.endswith(".py") and g.workspace.exists(p)]
    if not files:
        return GateResult("", True, "no python files")
    res = await arun_python(["-m", "py_compile", *files], g.workspace.root, timeout=60)
    return GateResult("", res.ok, f"{len(files)} files compile" if res.ok else res.tail(8))


@gate("lint")
async def _lint(g: GateContext, scope: str) -> GateResult:
    files = (
        ["."] if scope == "all" else [p for p in g.node.writes if p.endswith(".py") and g.workspace.exists(p)]
    )
    if not files:
        return GateResult("", True, "nothing to lint")
    res = await arun_python(["-m", "ruff", "check", "--no-cache", "-q", *files], g.workspace.root)
    return GateResult("", res.ok, "ruff clean" if res.ok else res.tail(12))


@gate("tests")
async def _tests(g: GateContext, _: str) -> GateResult:
    targets = [t for t in g.node.params.get("verify", []) if g.workspace.exists(t)]
    if not targets:
        return GateResult("", True, "no verification tests declared")
    res = await arun_python(pytest_args(targets), g.workspace.root, timeout=300)
    last = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else res.stderr[-300:]
    failed = [ln for ln in res.stdout.splitlines() if ln.startswith(("FAILED", "ERROR"))]
    return GateResult("", res.ok, f"{targets}: {last}" + (f" | {failed[:3]}" if failed else ""))


@gate("tests_pass")
async def _tests_pass(g: GateContext, _: str) -> GateResult:
    rep = g.store.get("test_report")
    return GateResult(
        "",
        rep["ok"] and rep["failed"] == 0 and rep["total"] > 0,
        f"{rep['passed']} passed, {rep['failed']} failed, {rep['errors']} errors"
        + (f" | {rep['failures'][:3]}" if rep["failures"] else ""),
    )


@gate("coverage_min")
async def _coverage(g: GateContext, arg: str) -> GateResult:
    minimum = float(arg or g.book.get("coverage_min", 80))
    cov = g.store.get("test_report")["coverage"] or 0
    return GateResult("", cov >= minimum, f"coverage {cov}% (min {minimum}%)")


@gate("lint_clean")
async def _lint_clean(g: GateContext, _: str) -> GateResult:
    issues = g.store.get("review_report")["lint"]
    return GateResult("", not issues, "no lint issues" if not issues else "; ".join(issues[:5]))


@gate("security_clean")
async def _security_clean(g: GateContext, _: str) -> GateResult:
    findings = g.store.get("review_report")["security"]
    return GateResult(
        "",
        not findings,
        "no security findings"
        if not findings
        else "; ".join(f"{f['path']}: {f['message']}" for f in findings[:5]),
    )


@gate("openapi_valid")
async def _openapi_valid(g: GateContext, _: str) -> GateResult:
    spec = json.loads(g.workspace.read("openapi.json"))
    design = g.store.get("design")
    have = {f"{m.upper()} {p}" for p, ms in spec["paths"].items() for m in ms}
    missing = sorted(set(design["endpoints"]) - have)
    return GateResult(
        "",
        not missing and spec.get("openapi", "").startswith("3"),
        f"{len(have)} operations; all designed endpoints present"
        if not missing
        else f"designed but not implemented: {missing}",
    )


@gate("docs_complete")
async def _docs_complete(g: GateContext, _: str) -> GateResult:
    files = g.store.get("docs")["files"]
    missing = [f for f in files if not g.workspace.exists(f)]
    return GateResult("", not missing, f"{len(files)} docs written" if not missing else f"missing {missing}")


@gate("readiness")
async def _readiness(g: GateContext, _: str) -> GateResult:
    rd = g.store.get("release_readiness")
    failed = [c["check"] for c in rd["checks"] if not c["passed"]]
    return GateResult("", rd["ready"], "all release checks pass" if not failed else f"failing: {failed}")


SMOKE_SCRIPT = """
import importlib, json, sys
from fastapi.testclient import TestClient
factory, steps = sys.argv[1], json.loads(sys.argv[2])
module, attr = factory.split(":")
client = TestClient(getattr(importlib.import_module(module), attr)(), follow_redirects=False)
saved = {}
for step in steps:
    path = step["path"].format(**saved)
    resp = client.request(step["method"], path, json=step.get("json"), headers=step.get("headers"))
    if resp.status_code != step["expect"]:
        print(f"{step['method']} {path}: expected {step['expect']} got {resp.status_code} {resp.text[:200]}")
        sys.exit(1)
    if step.get("save"):
        saved[step["save"]] = resp.json()[step["save"]]
    print(f"ok {step['method']} {path} -> {resp.status_code}")
"""


@gate("smoke")
async def _smoke(g: GateContext, _: str) -> GateResult:
    book = g.book
    steps = book.get("smoke", [])
    env = {k: v.replace("{run_dir}", str(g.run_dir)) for k, v in book.get("smoke_env", {}).items()}
    res = await arun_python(
        ["-c", SMOKE_SCRIPT, book["docs"]["openapi_factory"], json.dumps(steps)],
        g.workspace.root,
        timeout=120,
        env=env,
    )
    return GateResult("", res.ok, res.tail(6))
