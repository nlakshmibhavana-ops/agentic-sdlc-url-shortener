# Release 1.0.0

URL shortener service, from scratch

## Changes

- Project scaffold, configuration and entry point
- Unit tests from acceptance criteria (codes, validation, rate limit, service)
- API contract tests (status codes, auth, ownership, idempotency, rate limit)
- Random base62 code generator and custom-alias rules
- Target-URL validation (scheme allowlist, credentials, private hosts, loops)
- Token-bucket rate limiter with bounded memory
- SQLite schema, migrations and link/click repository
- Link service (create/resolve/delete/stats, idempotency, expiry, ownership)
- HTTP API, auth, error mapping, request IDs, health/readiness

## Evidence

- [x] all tests pass: 64 passed / 0 failed
- [x] coverage >= 90%: 96.4%
- [x] lint clean: 0 issues
- [x] no security findings: 0 findings
- [x] docs generated: openapi.json, docs/API.md, docs/DESIGN.md, CHANGELOG.md, README.md
- [x] no blocking questions open: []
