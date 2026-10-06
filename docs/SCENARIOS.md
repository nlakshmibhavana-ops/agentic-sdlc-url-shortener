# Scenarios

All three are reproducible with `./scripts/demo.sh`. The numbers below come from that
script's runs: `runs/demo-*/report.md` and `metrics.json`. Times are wall clock on a
laptop. "Active" time excludes time spent waiting for a human.

| | Greenfield | Brownfield | Ambiguous |
|---|---|---|---|
| Input | numbered, testable spec | three tickets against a legacy codebase | one vague sentence plus "fast" |
| Planned tasks | 9 | 4 | 2 → 3 after clarification |
| Nodes succeeded | 17 | 13 | 13 (1 checkpoint superseded) |
| Retries / rollbacks | 0 / 0 | 1 / 1 | 0 / 0 |
| MTTR | - | 1.5 s | - |
| Re-plans / reused nodes | 0 / 0 | 0 / 0 | 1 / 2 |
| Human actions | 1 approval | 2 approvals | 1 answer + 2 approvals |
| Product tests (coverage) | 64 (96.4%) | 22 (99.0%) | 79 (96.9%) |
| Active time | 11 s | 12 s | 20 s |
| Release | 1.0.0 | 0.4.2 → 0.5.0 | 1.0.0 → 1.1.0 |

---

## 1. Greenfield: URL shortener from a well-defined spec

**Input** (`scenarios/greenfield.yaml`): versioned REST API, API-key auth, custom aliases,
expiry, 302/404/410 semantics, owner-scoped analytics with no personal data, unsafe-target
rejection, 60/min rate limit, idempotent create, health and readiness probes, request IDs.

**Requirement understanding.** Eight capabilities were extracted, each with the sentence
that evidences it and its acceptance criteria. There are **zero ambiguity questions**:
"60 per minute" is measurable, and "store no IP addresses" pre-answers the privacy
question that the ambiguous scenario has to ask.

**Decomposition** (planner output, 9 tasks):

| Task | Depends on | Verified by |
|---|---|---|
| `scaffold` | - | compile, lint |
| `tests.unit` (test-first) | - | compile, lint |
| `tests.integration` | tests.unit | compile, lint |
| `impl.codes`, `impl.validation`, `impl.ratelimit` | scaffold, tests.unit | each module's own unit tests |
| `impl.storage` | scaffold | compile, lint |
| `impl.service` | storage, codes, validation, tests.unit | `test_service.py` |
| `impl.api` | service, ratelimit, tests.integration | the whole suite |

**Orchestration.** `codes`, `validation`, `ratelimit` and `storage` run as **parallel
branches**; the test suite asserts that their spans overlap. `service` and `api` are
synchronisation points. `test`, `review` and `docs` run as the join after `impl.api`,
`readiness` gathers their evidence, and `release` is a diamond (human approval).

**Validation.** Per task: compile, ruff and the task's acceptance tests. At the join: the
full suite with coverage (64 passed, 96.4%), lint, a security scan, OpenAPI (every
designed endpoint must exist in the generated schema) and docs completeness. Readiness
then checks six criteria, and release runs a live smoke test (health → create → redirect
→ stats) before asking for approval.

**Policy in action.** The first version of the run was safe-stopped by `SEC-001` on
`API_KEY = "test-key-1"` in a test fixture. The guardrail worked as designed, but it was a
false positive, so placeholder values are now allowlisted, as mainstream secret scanners
do. A second run paused on `CHG-004` (diff budget) because of the generated
`openapi.json`, so generated files no longer count toward the human-review budget. Both
are recorded in the engineering summary as tuning decisions.

**Output** (`runs/<run>/workspace/`):

* `shortener/`: config, db (WAL, forward-only migrations), codes, validation, rate
  limiter, repository, service, API
* tests: 64, plus a small browser page at `/` for trying the API
* `openapi.json`, `docs/API.md`, `docs/DESIGN.md` (seven ADRs), README, CHANGELOG,
  RELEASE_NOTES, VERSION

---

## 2. Brownfield: linkly v0.4 → v0.5

**Input** (`scenarios/brownfield.yaml`):

* LINK-142: a P1 security issue (`javascript:` and `data:` URLs become stored XSS)
* LINK-151: click counts are under-reported
* LINK-150: optional expiry with 410 Gone
* Constraint: existing clients and the live v0.4 database must keep working

**Requirement understanding.** Three capabilities. The analyst correctly does *not* treat
"POST /shorten" (mentioned in the security ticket) or "click counts" (mentioned in the
bug) as requests for new link creation or analytics: specific capabilities refine generic
ones.

**Codebase reasoning** (`analyze` stage, AST-based):

* Import graph `linkly.main → linkly.store`, blast radius `{main, store}`, data at risk
  `urls`.
* Routes per capability, for example `url_safety → POST /shorten` and
  `expiry → GET /r/{code}, GET /info/{code}`.
* Regression tests: `tests/test_api.py`. It is found through its `conftest` fixtures,
  since it doesn't import the modules directly.
* Latent defects found by static analysis, before any test ran:
  * `store.hit` does a **lost-update** read-modify-write: the actual root cause of
    LINK-151.
  * `main.go` returns a **cacheable 301**: the second root cause. Browsers never re-hit
    the service.
  * `store.create` derives **enumerable** codes from row ids. This one is out of scope:
    a decision records why, and it is carried into the release notes as a follow-up risk.

**Decomposition**: `tests.regression` (test-first) → `impl.url_validation` →
`impl.click_accuracy` → `impl.expiry`. The planner **serialised** `click_accuracy` after
`url_validation` because both edit `linkly/main.py` (decision D006). Each task's
verification is its own tests plus the regression tests selected for the modules it
touches.

**Orchestration: failure, rollback, retry and approval.**

1. `impl.expiry`, candidate 1, adds `expires_at` to `CREATE TABLE`. It is correct for a
   fresh database but silently broken for the live one. The exit gate's
   `test_migration.py::test_upgrade_existing_database` fails, so the change is **rolled
   back** (undo log) and the failure output goes to the agent as feedback.
2. Candidate 2 adds an idempotent `ALTER TABLE … ADD COLUMN` migration and passes all
   gates. `DATA-001` (DDL change against an existing database) then requires **human
   approval**. The approver sees the change digest and the passing gate evidence.
3. `resume`: the gates are re-checked at approval time, then the change is committed.
4. `release` bumps `pyproject.toml` (`CHG-002`, protected file), runs the smoke test
   (including "a `javascript:` URL is rejected with 400"), gets approved, and ships 0.5.0.

Metrics: 1 retry, 1 rollback, rollback rate 1/7, MTTR 1.5 s. The defective attempt never
reached a human.

---

## 3. Ambiguous: "Make short links safer and add some analytics so marketing can see how links perform. Redirects should also be fast."

The base is the released v1 service, an existing system with live data.

**Requirement understanding: ambiguity becomes explicit questions.**

| Id | Trigger | Blocking? | Options | Before the answer |
|---|---|---|---|---|
| Q-PRIVACY | "analytics … how links perform" | **yes**: personal data is never assumed | hashed / raw / none | open, so dependent work is gated |
| Q-PERF | "fast" (no number) | no | p95 < 50 ms in-process / p95 < 10 ms (CDN, infra) | assumed p95 < 50 ms |
| Q-SAFETY | "safer" | no | baseline / denylist / reputation API | assumed denylist |

The assumptions appear in the report, the design record and the **release notes**, so they
are reviewable after the fact. `url_safety`, `analytics` and `redirect` are recognised as
**already implemented** (decision: "No new work for …").

**Orchestration: partial progress, pause, re-plan.**

* **Plan v1**: `perf.redirect_cache`, then `safety.domain_denylist` (serialised: both
  edit `service.py` and `config.py`), plus a **checkpoint node** `clarify.Q-PRIVACY` with
  the human gate `clarified:Q-PRIVACY`.
* Session 1: both unblocked tasks are implemented and verified (including a p95 < 50 ms
  redirect perf test), then the run **pauses** in `waiting` on the question.
* `answer Q-PRIVACY hashed`: `human_answers v2` invalidates `requirements`, which
  cascades to `analyze`, `design` and `plan`.
* **Plan v2** (`plan.revised`):

  ```
  added   [analytics.unique_visitors]
  removed [clarify.Q-PRIVACY]
  reused  [perf.redirect_cache, safety.domain_denylist]   # not re-run
  ```

* `analytics.unique_visitors` (daily-rotating HMAC of IP and UA; the raw IP never leaves
  the function) passes its gates, then waits for **approval** under `DATA-001`
  (migration 0002 on the live database).
* After approval, the join stages re-run on the combined change: 79 tests pass at
  96.9%, docs are regenerated, and the 1.1.0 release is approved.

**What if the human answers "raw"?** The plan selects the raw-IP task, and the
**privacy guardrail** (`PII-001`) blocks the `client_ip TEXT` column on every attempt.
The node fails and the run safe-stops: release is blocked and nothing reaches the
workspace. This is covered by
`tests/test_engine.py::test_choosing_raw_ip_storage_is_stopped_by_the_privacy_guardrail`.
A human can't approve their way past a `block` rule; that requires changing policy, which
is itself change-controlled.

**An exposed engine bug, fixed.** The first version of this scenario failed after
re-planning. Two causes:

* Regression-test selection read the *current* workspace, which now included tests the
  run had just added. That changed the cache task's spec, so the task was treated as
  changed.
* Its revert was refused because a later task had edited the same file, but the engine
  re-ran it anyway.

The fixes:

* Regression tests are drawn from the baseline system only.
* A change that can't be reverted safely is kept and flagged for a human instead of
  being re-applied.

Both are now covered by the scenario's test.
