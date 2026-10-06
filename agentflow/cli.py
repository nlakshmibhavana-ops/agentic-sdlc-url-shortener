"""Command-line interface: python -m agentflow <command>.

In a terminal, `run` is interactive: it shows each step live, stops to ask you when a
human decision is needed (a clarifying question or an approval), then carries on. With
--no-input (or when not attached to a terminal) it pauses instead and you continue with
`answer` / `approve` + `resume`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import audit, metrics, playbooks
from . import scenario as scenario_mod
from .approvals import ApprovalBroker
from .audit import AuditLog
from .engine import Orchestrator, RunState, create_run

RUNS = Path(os.environ.get("AGENTFLOW_RUNS", scenario_mod.ROOT / "runs"))
# The exact interpreter running us, so printed commands work without activating the venv.
_exe = Path(sys.executable)
PY = str(_exe.relative_to(Path.cwd())) if _exe.is_relative_to(Path.cwd()) else str(_exe)
AF = f"{PY} -m agentflow"
COLOR = sys.stdout.isatty()


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def bold(t: str) -> str:
    return c(t, "1")


def green(t: str) -> str:
    return c(t, "32")


def red(t: str) -> str:
    return c(t, "31")


def yellow(t: str) -> str:
    return c(t, "33")


def dim(t: str) -> str:
    return c(t, "2")


def _run_dir(run_id: str) -> Path:
    path = RUNS / run_id
    if not path.exists():
        matches = sorted(RUNS.glob(f"*{run_id}*"))
        if len(matches) != 1:
            sys.exit(f"run '{run_id}' not found (or ambiguous)")
        path = matches[0]
    return path


def _llm(provider: str):
    if provider == "claude":
        from .agents.llm import ClaudeClient

        return ClaudeClient()
    if provider == "ollama":
        from .agents.ollama import OllamaClient

        client = OllamaClient()
        try:
            asyncio.run(client.check())
        except Exception as exc:  # the run still works: every LLM agent falls back
            print(red(f"Ollama unavailable ({exc}); LLM agents will fall back to deterministic ones."))
        print(dim(f"LLM provider: {client.describe()}"))
        return client
    return None


# --------------------------------------------------------------------- live progress


class Progress:
    """Turns audit events into one readable line per meaningful step."""

    def __init__(self) -> None:
        self.t0 = time.time()

    def __call__(self, e: dict) -> None:
        ev, node, d = e["event"], e["node"] or "", e["data"]
        ts = dim(f"[{time.time() - self.t0:5.1f}s]")
        line = None
        if ev == "attempt.start":
            n = f" (attempt {d['attempt']})" if d["attempt"] > 1 else ""
            line = f"{dim('->')} {node}{n} {dim('agent=' + d['agent'])}"
        elif ev == "agent.note":
            line = f"   {dim(node + ': ' + d['note'][:110])}"
        elif ev == "change.applied":
            line = f"   {node}: applied change to {', '.join(d['paths'][:4])} (+{d['added']}/-{d['removed']})"
        elif ev == "gate.exit" and not d["passed"]:
            line = f"   {red('x')} {node}: check '{d['gate']}' failed: {d['detail'][:120]}"
        elif ev == "change.rolled_back":
            line = f"   {yellow('<-')} {node}: change rolled back {d.get('reason', '')}"
        elif ev == "attempt.retry":
            line = f"   {yellow('retry')} {node}: trying again (attempt {d['next_attempt']})"
        elif ev == "policy.blocked":
            line = f"   {red('BLOCKED by policy')} {node}: {d['reason'][:120]}"
        elif ev == "node.succeeded":
            line = f"{green('ok')} {node}"
        elif ev == "node.failed":
            line = f"{red('FAILED')} {node}: {str(d.get('error'))[:140]}"
        elif ev == "node.waiting":
            line = f"{yellow('..')} {node} is waiting for you"
        elif ev == "plan.created":
            line = f"   plan v1: {len(d['added'])} tasks: {', '.join(d['added'])}"
        elif ev == "plan.revised":
            line = (
                f"   {bold('re-plan')} v{d['plan_version']}: added {d['added']}, removed {d['removed']}, "
                f"kept without re-running {d['reused']}"
            )
        elif ev == "node.invalidated":
            line = f"   {dim(node + ' must re-run: ' + d['reason'])}"
        elif ev == "run.safe_stop":
            line = red(f"SAFE STOP: {d['reason']}")
        if line:
            print(f"{ts} {line}", flush=True)


# --------------------------------------------------------------------- human prompts


def _ask(prompt: str, choices: list[str]) -> str:
    while True:
        answer = input(prompt).strip().lower()
        if answer in choices:
            return answer
        print(f"  please type one of: {', '.join(choices)}")


def _ask_humans(run_dir: Path, who: str) -> bool:
    """Prompt for every open question and pending approval. Returns True if anything was decided."""
    st = RunState(run_dir)
    acted = False
    req = st.store.get("requirements") or {}
    for qid in req.get("blocking_open", []):
        q = next(q for q in req["questions"] if q["id"] == qid)
        print(bold(f"\n=== QUESTION {qid} (the agents will not guess this) ==="))
        print(f'Found in the request: "{q["evidence"]}"')
        print(q["text"])
        for key, label in q["options"].items():
            print(f"  {bold(key):<20} {label}")
        choice = _ask(f"Your answer [{'/'.join(q['options'])}]: ", list(q["options"]))
        Orchestrator(run_dir).submit_answer(qid, choice, who)
        print(green(f"Recorded {qid} = {choice}. The plan will be revised."))
        acted = True
    broker = ApprovalBroker(run_dir / "approvals.json", AuditLog(run_dir / "audit.jsonl", st.run_id))
    for req_ in broker.pending():
        print(bold(f"\n=== APPROVAL NEEDED: {req_['node']} ==="))
        print("Why a human must approve:")
        for reason in req_["reasons"]:
            print(f"  - {reason}")
        print(req_["summary"])
        diffs = sorted((run_dir / "changes").glob(f"{req_['node']}.a*.diff"))
        if diffs:
            print(dim(f"Full diff: {diffs[-1]}"))
        choice = _ask("Approve? [y/n/d=show diff]: ", ["y", "n", "d"])
        if choice == "d" and diffs:
            print(diffs[-1].read_text())
            choice = _ask("Approve? [y/n]: ", ["y", "n"])
        comment = input("Comment (optional): ").strip()
        broker.decide(req_["node"], choice == "y", who, comment)
        print(green("Approved.") if choice == "y" else red("Rejected: the change will be rolled back."))
        acted = True
    return acted


# --------------------------------------------------------------------- run / summary


def _execute(run_dir: Path, max_parallel: int, interactive: bool, who: str) -> int:
    while True:
        st = RunState(run_dir)
        orch = Orchestrator(
            run_dir,
            "pause",
            max_parallel,
            _llm(st.scenario.get("provider", "deterministic")),
            on_event=Progress(),
        )
        status = asyncio.run(orch.execute())
        if status == "waiting" and interactive and _ask_humans(run_dir, who):
            print(bold("\nContinuing...\n"))
            continue
        break
    _summary(run_dir)
    return 0 if status in ("succeeded", "waiting") else 1


def _summary(run_dir: Path) -> None:
    st = RunState(run_dir)
    color = {"succeeded": green, "waiting": yellow}.get(st.status, red)
    print("\n" + bold("=" * 70))
    print(
        f"Run {st.run_id}: {color(st.status.upper())}" + (f"  ({st.stop_reason})" if st.stop_reason else "")
    )
    print(bold("=" * 70))
    if st.status == "waiting":
        _print_pending(run_dir)
    ws = run_dir / "workspace"
    book = playbooks.load(st.scenario["playbook"])
    tests = st.store.get("test_report")
    print(f"\n{bold('Code built by this run:')}  {ws}")
    for p in sorted(ws.iterdir()):
        print(f"   {p.name}{'/' if p.is_dir() else ''}")
    if tests:
        print(
            f"\n{bold('Tests:')} {tests['passed']} passed, {tests['failed']} failed, "
            f"coverage {tests['coverage']}%"
        )
    rel = st.store.get("release")
    if rel and st.status == "succeeded":
        print(f"{bold('Released version:')} {rel['version']}")
    print(f"\n{bold('Evidence report:')}  {run_dir / 'report.md'}")
    if st.status == "succeeded":
        print(f"\n{bold('Try the service:')}  {AF} serve {st.run_id}")
        if book["package"] == "shortener":
            print(dim("   then open http://localhost:8000/ in a browser (or use curl):"))
            print(
                dim(
                    "   curl -s -XPOST localhost:8000/api/v1/links -H 'X-API-Key: dev-key' "
                    '-H \'Content-Type: application/json\' -d \'{"url":"https://example.com","alias":"demo"}\''
                )
            )
            print(dim("   curl -si localhost:8000/demo | head -3        # 302 redirect"))
            print(dim("   http://localhost:8000/docs                     # interactive API docs"))


def _print_pending(run_dir: Path) -> None:
    st = RunState(run_dir)
    broker = ApprovalBroker(run_dir / "approvals.json", AuditLog(run_dir / "audit.jsonl", st.run_id))
    for req in broker.pending():
        print(f"\nApproval needed for '{req['node']}': {'; '.join(req['reasons'])}")
        print(f"  {AF} approve {st.run_id} {req['node']} --by <your-name>")
    req = st.store.get("requirements") or {}
    for qid in req.get("blocking_open", []):
        q = next(q for q in req["questions"] if q["id"] == qid)
        print(f"\nQuestion {qid}: {q['text']}")
        for key, label in q["options"].items():
            print(f"    {key:<10} {label}")
        print(f"  {AF} answer {st.run_id} {qid} <option> --by <your-name>")
    print(f"then: {AF} resume {st.run_id}")


def _status(run_dir: Path) -> None:
    st = RunState(run_dir)
    for nid in st.graph.topological_order():
        n = st.graph[nid]
        print(
            f"  {n.status.value:<10} {nid:<28} attempts={n.attempts} {(n.wait_reason or n.error or '')[:100]}"
        )
    _summary(run_dir)


def _serve(run_dir: Path, port: int, host: str = "127.0.0.1") -> int:
    st = RunState(run_dir)
    book = playbooks.load(st.scenario["playbook"])
    env = {k: v.replace("{run_dir}", str(run_dir)) for k, v in book.get("serve_env", {}).items()}
    if book["package"] == "shortener":
        env["SHORTENER_BASE_URL"] = f"http://localhost:{port}"
    factory = book["docs"]["openapi_factory"]
    print(f"Serving {factory} from {run_dir / 'workspace'} on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    for k, v in env.items():
        print(f"  {k}={v}")
    print(f"  API docs: http://127.0.0.1:{port}/docs")
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print(red(f"uvicorn is not installed for {sys.executable}."))
        print(
            f"Use the project's environment: {scenario_mod.ROOT / '.venv/bin/python'} -m agentflow serve "
            f"{st.run_id}  (or `source .venv/bin/activate` first)"
        )
        return 1
    print(f"  Home page: http://127.0.0.1:{port}/")
    cmd = [sys.executable, "-m", "uvicorn", "--factory", factory, "--host", host, "--port", str(port)]
    try:
        return subprocess.call(cmd, cwd=run_dir / "workspace", env={**os.environ, **env})  # noqa: S603
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="agentflow", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, text in (
        ("run", "start a scenario (greenfield | brownfield | ambiguous)"),
        ("resume", "continue a paused or stopped run"),
    ):
        s = sub.add_parser(name, help=text)
        s.add_argument("scenario" if name == "run" else "run")
        s.add_argument("--no-input", action="store_true", help="never prompt; pause for human actions")
        s.add_argument(
            "--as",
            dest="who",
            default=os.environ.get("USER", "operator"),
            help="your name for the audit trail",
        )
        s.add_argument("--max-parallel", type=int, default=4)
        if name == "run":
            s.add_argument(
                "--provider", choices=["deterministic", "claude", "ollama"], default="deterministic"
            )
            s.add_argument("--run-id")
    a = sub.add_parser("approve", help="record an approval decision (non-interactive)")
    a.add_argument("run")
    a.add_argument("node")
    a.add_argument("--by", required=True)
    a.add_argument("--reject", action="store_true")
    a.add_argument("--comment", default="")
    q = sub.add_parser("answer", help="answer a clarifying question (non-interactive)")
    q.add_argument("run")
    q.add_argument("question")
    q.add_argument("option")
    q.add_argument("--by", required=True)
    sv = sub.add_parser("serve", help="start the service a run built")
    sv.add_argument("run")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--host", default="127.0.0.1", help="0.0.0.0 inside a container")
    sub.add_parser("demo", help="run all three scenarios non-interactively (scripts/demo.sh)")
    for name, text in (
        ("status", "show run status"),
        ("stop", "request a safe stop"),
        ("verify", "verify the audit hash chain"),
        ("report", "regenerate report.md"),
    ):
        sub.add_parser(name, help=text).add_argument("run")
    sub.add_parser("metrics", help="aggregate reliability metrics across runs")
    sub.add_parser("list", help="list runs")
    args = p.parse_args(argv)

    if args.cmd in ("run", "resume"):
        interactive = sys.stdin.isatty() and not args.no_input
        if args.cmd == "run":
            sc = scenario_mod.load(args.scenario)
            sc["provider"] = args.provider
            run_dir = create_run(sc, RUNS, args.run_id)
            print(bold(f"Run {run_dir.name}: {sc['title']}"))
            print(dim("Requirement:\n  " + sc["requirement"].strip().replace("\n", "\n  ")) + "\n")
        else:
            run_dir = _run_dir(args.run)
        return _execute(run_dir, args.max_parallel, interactive, args.who)
    if args.cmd == "approve":
        run_dir = _run_dir(args.run)
        st = RunState(run_dir)
        broker = ApprovalBroker(run_dir / "approvals.json", AuditLog(run_dir / "audit.jsonl", st.run_id))
        decision = broker.decide(args.node, not args.reject, args.by, args.comment)
        print(f"{args.node}: {decision['status']} by {args.by}. Run `resume` to continue.")
        return 0
    if args.cmd == "answer":
        Orchestrator(_run_dir(args.run)).submit_answer(args.question, args.option, args.by)
        print(f"{args.question} = {args.option}. Run `resume` to re-plan and continue.")
        return 0
    if args.cmd == "serve":
        return _serve(_run_dir(args.run), args.port, args.host)
    if args.cmd == "demo":
        script = scenario_mod.ROOT / "scripts" / "demo.sh"
        return subprocess.call(["bash", str(script)], env={**os.environ, "PY": sys.executable})  # noqa: S603,S607
    if args.cmd == "status":
        _status(_run_dir(args.run))
        return 0
    if args.cmd == "stop":
        (_run_dir(args.run) / "STOP").touch()
        print("stop requested; the run halts safely at the next scheduling step")
        return 0
    if args.cmd == "verify":
        ok, _, msg = audit.verify(_run_dir(args.run) / "audit.jsonl")
        print(("OK: " if ok else "TAMPERED: ") + msg)
        return 0 if ok else 2
    if args.cmd == "report":
        from .report import write_report

        print(write_report(_run_dir(args.run)))
        return 0
    if args.cmd == "metrics":
        print(json.dumps(metrics.aggregate(RUNS), indent=2))
        return 0
    if args.cmd == "list":
        for state in sorted(RUNS.glob("*/state.json")):
            data = json.loads(state.read_text())
            print(f"{data['run_id']:<45} {data['status']}")
        return 0
    return 1
