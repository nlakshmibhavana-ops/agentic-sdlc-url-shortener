# agentflow: governed agentic SDLC for a URL shortener

A working prototype of an **agentic software-engineering system**. It takes a
requirement (well-defined, a brownfield ticket, or a vague product ask) and drives it
through requirements → design → plan → implementation → test → review → docs →
release readiness → release. Agents do the multi-step work; a governed orchestration
layer decides what is accepted, and humans own the approvals and final quality.

The demo product is a **URL shortener**. The system builds it from scratch, upgrades a
legacy shortener with real defects, and turns an ambiguous ask into a clarified,
released change.

| Deliverable | Where |
|---|---|
| Working prototype (runnable end to end) | `agentflow/` + `scripts/demo.sh` |
| Architecture overview (components, orchestration model, control flow, decisions) | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Three scenarios: greenfield, brownfield, ambiguous | [docs/SCENARIOS.md](docs/SCENARIOS.md) + `scenarios/*.yaml` |
| Setup instructions | below |
| Testing approach, risks, trade-offs, assumptions, limitations | [docs/ENGINEERING_SUMMARY.md](docs/ENGINEERING_SUMMARY.md) |
| Generated product (service, tests, OpenAPI, docs) | `runs/<run>/workspace/` after a run |
| Per-run evidence (report, audit trail, diffs, metrics) | `runs/<run>/` |

## Setup

Requires Python 3.11+ (developed on 3.12).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .                 # add '.[claude]' for the optional Claude backend
pytest                           # 43 orchestrator tests, ~90 s (they run real scenarios)
python -m agentflow run greenfield   # interactive: watch it build, approve the release
./scripts/demo.sh                # all three scenarios, non-interactive, ~1 min
```

No API key is needed. By default the agents are deterministic (see "Two providers"
below), so every run is reproducible.

## Quick start: run a scenario yourself

```bash
python -m agentflow run greenfield
```

What happens:

1. Each step prints live as the agents work: requirements → design → plan → the tasks
   (several in parallel) → tests, review and docs → release readiness. A failed check
   shows a red `x` and a `<- change rolled back` line, followed by a retry.
2. When a **human decision** is needed, the run stops and asks you in the terminal:
   * a **question** the agents must not guess (the ambiguous scenario asks what may be
     stored about visitors), or
   * an **approval** (a schema change on a live database, or the release), showing why
     approval is required, the files changed, the test results, and an option to view
     the full diff.

   Your answer is recorded under your name (`--as NAME`, default `$USER`) and the run
   continues.
3. At the end it prints **where the code was built** (`runs/<run-id>/workspace/`), the
   test results, the released version and the evidence report. Then start the service
   it built:

```bash
python -m agentflow serve <run-id>          # http://localhost:8000/docs
curl -s -XPOST localhost:8000/api/v1/links -H 'X-API-Key: dev-key' \
     -H 'Content-Type: application/json' -d '{"url":"https://example.com","alias":"demo"}'
curl -si localhost:8000/demo | head -3      # 302 -> https://example.com
curl -s localhost:8000/api/v1/links/demo/stats -H 'X-API-Key: dev-key'
```

Things to try:

| Command | What you'll see |
|---|---|
| `run greenfield` | 9 tasks, parallel branches, 1 question for you: approve the release |
| `run brownfield` | static analysis finds the real bugs; the first expiry fix breaks the existing database, is rolled back and retried; you approve the migration and the release |
| `run ambiguous` | 3 ambiguities found; you answer the privacy question; the plan is revised and keeps finished work; you approve the migration and the release |
| `run ambiguous`, answer `raw` | the privacy guardrail blocks storing raw IPs and the run safe-stops |
| answer `n` at an approval | the change is rolled back and the run stops |

**Non-interactive** (CI or scripts): add `--no-input`. The run then stops at each human
step and tells you what to type, for example
`python -m agentflow approve <run> release --by alice` followed by
`python -m agentflow resume <run>`. `scripts/demo.sh` works this way.

### CLI

| Command | Purpose |
|---|---|
| `run <scenario> [--as NAME] [--no-input] [--provider deterministic\|ollama\|claude]` | start a run (interactive in a terminal) |
| `resume <run> [--no-input]` | continue after a pause, stop or crash |
| `serve <run> [--port 8000]` | start the service the run built |
| `answer <run> <question> <option> --by NAME` | resolve a clarifying question (triggers re-planning) |
| `approve <run> <node> --by NAME [--reject] [--comment]` | human approval checkpoint |
| `status <run>` / `report <run>` | state, pending human actions / regenerate report |
| `stop <run>` | safe stop (also: SIGINT, or a `STOP` file in the run dir) |
| `verify <run>` | verify the tamper-evident audit chain |
| `metrics` | reliability metrics aggregated across all runs |

## What a run produces

```
runs/<run-id>/
  report.md        requirement understanding, impact analysis, ADRs, plan + traceability,
                   mermaid DAG, timeline, gate results, policy findings, approvals,
                   decision log, test/review/readiness evidence, lineage, metrics
  audit.jsonl      append-only, hash-chained events with trace/span ids
  state.json       resumable state (graph, artifacts with versions/lineage, decisions)
  approvals.json   approval requests and decisions, bound to change digests
  metrics.json     success rate, retries, rollbacks, MTTR, latency, approvals, re-plans
  changes/*.diff   every change set applied, including rolled-back attempts
  artifacts/*.json every artifact version (requirements, impact, design, plan, reports)
  workspace/       the product: code, tests, openapi.json, docs/, CHANGELOG, RELEASE_NOTES
```

## Three providers

* **deterministic** (default): agents reason with a governed knowledge base, and the
  implementation agents draw reviewed change sets from `agentflow/playbooks/`. Runs are
  reproducible, so the demo, the tests and the reviewer all see the same behaviour.
* **ollama**: a local model does the requirements analysis and writes the code, with no
  API key and nothing leaving your network. The model's JSON is constrained by the
  agent's schema (Ollama `format`) and validated again before use. Tested with
  `qwen2.5-coder:7b` on a 16-core laptop CPU (about 13 tokens/s).
  ```bash
  ollama pull qwen2.5-coder:7b
  python -m agentflow run brownfield --provider ollama
  # remote Ollama: OLLAMA_BASE_URL=http://host:11434 (or an SSH tunnel); model: OLLAMA_MODEL
  ```
* **claude**: the same two agents call Claude (`claude-opus-5-5`) with structured outputs.
  Needs `ANTHROPIC_API_KEY` and `pip install -e '.[claude]'`.

In both LLM modes the implementation agent gets the task, its acceptance tests, the
current files and the previous attempt's gate failures. The first attempt is the model;
if its change fails policy or the gates, it is rolled back and the deterministic agent is
the **fallback** on the last attempt. Every provider goes through the same machinery:
policy, the write-set boundary, gates, approvals, rollback and audit. The orchestrator
never trusts an agent's output; it verifies it. The audit trail records which agent
(and so which model) produced each attempt. See
[docs/ENGINEERING_SUMMARY.md](docs/ENGINEERING_SUMMARY.md) for what each mode does and
does not prove.

## Repository layout

```
agentflow/
  engine.py        orchestrator: scheduling, gates, retries, fallback, approvals,
                   rollback, invalidation, re-planning, safe-stop, persistence
  graph.py         explicit dependency graph (cycle check, readiness, blocking, mermaid)
  scenario.py      SDLC stage templates; planner-inserted task nodes
  gates.py         entry/exit gates (artifact checks, compile, lint, tests, coverage,
                   OpenAPI, readiness, smoke) incl. human gates
  policy.py        guardrails: security, PII/compliance, change control, dependencies
  approvals.py     human checkpoints bound to change digests
  context.py       versioned artifacts with lineage + decision log
  audit.py         hash-chained audit trail with spans
  metrics.py       reliability metrics derived from the audit trail
  workspace.py     path-confined, all-or-nothing change sets with undo logs
  agents/          requirements, codebase analyst, design, planner, implement (+LLM),
                   test runner, reviewer, docs, release readiness, release
  knowledge.py     capability vocabulary, acceptance criteria, ADRs, ambiguity rules
  playbooks/       reviewed change catalogues (shortener v1, linkly upgrade, v2 features)
fixtures/linkly/   the legacy codebase for the brownfield scenario
scenarios/         greenfield.yaml, brownfield.yaml, ambiguous.yaml
tests/             orchestrator unit + end-to-end tests
```
