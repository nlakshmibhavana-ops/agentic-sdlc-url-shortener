# Engineering summary

## 1. Plan and rationale

The assignment's "critical differentiator" is the orchestration layer, so the build
order was:

1. **The product first, by hand, to a production bar.** I wrote the URL shortener
   (64 tests, 96% coverage, lint- and security-clean) and a deliberately flawed legacy
   service. These give the orchestrator real code to reason about, real tests to run and
   real defects to catch. An orchestrator demo over toy outputs proves little.
2. **The governance core next:**
   * the graph
   * gates that run real tools
   * policy evaluated before changes touch disk
   * digest-bound approvals
   * undo-log rollback
   * a hash-chained audit trail
   * versioned artifacts with lineage
3. **Agents last.** They are thin, replaceable producers of `AgentResult`. The engine,
   not the agent, decides what is accepted.
4. **Scenarios as acceptance tests for the orchestrator.** Each one forces a different
   path:
   * greenfield: parallel fan-out and fan-in
   * brownfield: codebase reasoning, a defective attempt, rollback, retry, approval of
     a data migration
   * ambiguous: clarification, partial progress, pause, dynamic re-planning with reuse

   Running them repeatedly found five real engine defects, all fixed and covered by
   tests (section 6).

**Controlled autonomy, concretely:**

| Agents may (autonomously) | Agents may not | Humans own |
|---|---|---|
| Interpret requirements, propose questions and options | Write outside the task's declared paths | Answers to blocking questions (personal data is never assumed) |
| Analyse code, design, decompose, order tasks | Add non-allowlisted dependencies, secrets, `eval`/`shell`, raw PII | Approval of schema changes on live data, protected files, large diffs |
| Produce change sets; retry with feedback; use the fallback agent | Apply a change (the engine applies it) | Approval of every release |
| Assume **non-blocking** defaults (recorded, surfaced in the release notes) | Exceed attempt or time budgets | Rejection, which rolls back; stop and resume |

## 2. Artifacts produced

**Per run** (`runs/<id>/`):

* `report.md`, the evidence pack. Its sections:
  * requirement understanding and traceability
  * impact analysis
  * ADRs
  * plan, live DAG and timeline
  * gates, policy findings and approvals
  * decision log and lineage
  * test, review and readiness reports
  * metrics
* `audit.jsonl` (hash-chained), `state.json`, `approvals.json` and `metrics.json`
* `changes/*.diff`, including rolled-back attempts
* `artifacts/*.json`, every version of every artifact

**Product** (`runs/<id>/workspace/`):

* service code
* unit and integration tests
* `openapi.json`, generated from the running app, so it can't drift
* `docs/API.md` and `docs/DESIGN.md` (ADRs, assumptions, out-of-scope items)
* `README.md`, `CHANGELOG.md`, `RELEASE_NOTES.md` (with the evidence checklist and
  residual risks) and `VERSION`

## 3. Risks, trade-offs and failure scenarios

| Failure scenario | Guardrail / handling | Evidence |
|---|---|---|
| Agent produces code that passes for new data but breaks existing data | Test-first regression tests incl. an upgrade-from-v0.4 test; exit gate fails → rollback → next candidate | brownfield `impl.expiry` attempt 1 |
| Agent output is unsafe (secret, `eval`, SQL injection, shell) | `SEC-*` block before apply; counts as a failed attempt with feedback | `tests/test_policy.py` |
| Agent touches files outside its task | `CHG-003` block (write-set boundary) | `tests/test_policy.py` |
| Human chooses a non-compliant option (store raw IPs) | `PII-001` block; humans can't approve past a block | `test_choosing_raw_ip_storage…` |
| Approved change differs from what is applied | Approval bound to the change digest; gates re-run at approval time | engine `_resume_pending` |
| Approver rejects | Rollback, node failed, downstream blocked, run halted | `test_rejected_approval…` |
| LLM provider outage, refusal, or malformed output | Structured output with schema validation; `AgentError` → fallback agent; server-side refusal fallback on the API | `test_llm_outage_falls_back…` |
| Runaway loop or cost | Per-node attempts, per-run attempt and time budgets → safe stop | `test_attempt_budget…` |
| Crash mid-change | Inflight undo log rolled back on resume; atomic state writes | `test_crash_between_apply…` |
| Operator needs to halt now | `stop` / STOP file / SIGINT → cancel, roll back in-flight, resumable | `test_safe_stop_then_resume` |
| Requirements change mid-run | Artifact invalidation → re-plan → reuse unchanged tasks; unsafe reverts escalate to a human | ambiguous scenario |
| Audit tampering | SHA-256 chain; `verify` detects edits and deletions | `test_audit_context.py` |
| Parallel tasks conflict | Planner serialises overlapping write sets; workspace mutations under one lock | decision D006 (brownfield) |

**Trade-offs I chose deliberately:**

* **Deterministic default provider.** Reproducibility for reviewers and CI, at the cost
  of the default agents being catalogue-driven rather than creative (see Limitations).
  The Claude path is wired through the same governance.
* **Approve after validation.** Better evidence for approvers and fewer pointless
  approvals. The cost: unapproved changes exist briefly in the *sandbox* workspace,
  never in a release.
* **One workspace lock.** Simple and consistent validation. Agent reasoning stays
  parallel, but apply-and-validate is serialised, which is a merge-queue throughput
  limit.
* **Keyword and AST heuristics** for requirements and codebase analysis. They are
  transparent and testable, but brittle against phrasing they weren't written for. That
  is exactly where the LLM provider adds value, and its output is still validated
  against the capability vocabulary.
* **SQLite plus an in-process rate limiter and cache in the product.** Zero-ops for a
  prototype, behind a repository boundary. Multi-instance deployment needs Postgres and
  Redis (recorded in the ADRs).

**Policy tuning found during the runs.** The first greenfield run was safe-stopped by
`SEC-001` on a test fixture's `"test-key-1"`, and a later one paused on `CHG-004` because
of the generated `openapi.json`. I allowlisted obvious placeholders and excluded
generated files from the review budget. Both relaxations are narrow, documented and
tested. Tuning false positives without disabling the rule is part of operating
guardrails.

## 4. Assumptions

* A "human approval" is a named person running `approve` (or answering the
  interactive prompt). There is no identity provider; the name is recorded, not
  authenticated.
* The workspace is a sandbox. "Release" means producing a versioned, smoke-tested,
  approved artifact. Deploying it is out of scope.
* The brownfield system's existing tests are its regression suite, and they are
  trustworthy.
* Non-blocking ambiguities may proceed on a recorded default. Anything touching personal
  data is blocking.
* Python/FastAPI is an acceptable stack. Nothing in the orchestrator is
  Python-specific except the gates' tool commands.

## 5. Limitations (honest)

* **In deterministic mode the implementation agents don't invent code.** They select
  from reviewed change catalogues (`agentflow/playbooks/`), including deliberately
  defective candidates. Everything around them is real:
  * policy, apply, rollback and gates
  * test execution
  * re-planning and approvals
  * audit and metrics

  Requirements analysis, codebase analysis, design, planning (selection, ordering,
  serialisation, regression selection, checkpoints), test running, review, docs and
  release are computed, not replayed.
* **The Claude path** (`--provider claude`) is implemented with the current SDK:
  structured outputs, adaptive thinking and server-side refusal fallback. Its
  *fallback* behaviour is tested with a simulated outage. I have **not** included live
  LLM runs: they need an API key and are non-deterministic. Expect more retries in that
  mode, which is what the retry, feedback and fallback machinery is for.
* Capability vocabulary and questions are a curated catalogue. A requirement outside it
  is rejected (`no recognisable capability`) rather than mis-handled. That is safe, but
  it limits generality.
* Codebase analysis is Python-only (AST). Impact hints per capability are keyword-based.
* Single-process orchestrator. Parallelism is asyncio within one run. There is no
  distributed workers, queue or multi-tenant isolation, and no sandboxing of executed
  tests beyond a subprocess with a timeout.
* Approver identity is not authenticated and there is no separation-of-duties policy
  beyond rejecting `agent`/`system` as approver names.
* Policy rules are heuristics: regexes plus AST. They are not a substitute for a real
  SAST/secret scanner (Semgrep, gitleaks) or a dependency vulnerability scan. Those
  would plug in as additional gates.

## 6. Testing approach

Three layers:

1. **Product tests** (inside each generated workspace, run by the orchestrator's gates):
   * shortener: 64 tests, 96% coverage (79 after the v2 changes)
   * linkly: 22 tests after the upgrade

   They cover validation abuse cases (javascript:, data:, credentials, private and
   metadata IPs, self-loops), concurrency (atomic click counting under 8 threads),
   expiry, idempotency, ownership isolation, rate limiting, migrations on existing
   databases, the perf budget and privacy (no raw IP anywhere in the DB dump).
2. **Orchestrator unit tests** (`tests/test_graph.py`, `test_workspace.py`,
   `test_policy.py`, `test_audit_context.py`, `test_agents.py`): graph invariants,
   all-or-nothing apply and path confinement, every policy rule including the DDL
   detector, audit tamper detection, lineage, requirement understanding and planning.
3. **Orchestrator end-to-end tests** (`tests/test_engine.py`). These drive whole
   scenarios with a scripted "human":
   * all three happy paths, with metric assertions (rollbacks, retries, re-plans, reuse)
   * PII block
   * rejected approval
   * safe stop and resume
   * budget exhaustion
   * crash recovery
   * LLM outage fallback
   * parallel-span overlap

`pytest` → 43 passed (~90 s). `ruff check .` is clean. `scripts/demo.sh` runs all three
scenarios end to end and verifies the audit chains.

**Engine defects the scenarios found during development** (each fixed and covered):

1. An approval-pending attempt was counted as a failure, which inflated MTTR.
2. Approval was requested before validation, so humans could be asked to approve code
   that later failed tests. The flow was reordered.
3. A schema change inside a multi-line SQL literal escaped the DATA-001 rule. The rule
   now compares DDL statements.
4. Re-planning treated tasks as changed when regression selection saw tests the run
   itself had added.
5. A refused revert was followed by a blind re-run.

## 7. What I would do next

* Run the Claude provider against the scenarios and record retry/fallback rates as a
  baseline. Add an eval set of requirements outside the catalogue.
* Replace the heuristic scanners with real tools (Semgrep, gitleaks, pip-audit) as gates.
  Add mutation testing as a gate on test-authoring tasks.
* Real identities for approvers (OIDC). Two-person rule for `DATA-*` and release.
* Run each attempt's validation in an isolated container. Distribute workers behind a
  queue, with the state store in Postgres.
* Product: Postgres plus Redis for multi-instance rate limiting and caching, async click
  ingestion, an OpenTelemetry exporter for the audit spans.
