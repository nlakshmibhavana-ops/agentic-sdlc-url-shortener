# Changelog

## [Unreleased] - URL shortener service, from scratch

- Project scaffold, configuration and entry point
- Unit tests from acceptance criteria (codes, validation, rate limit, service)
- API contract tests (status codes, auth, ownership, idempotency, rate limit)
- Random base62 code generator and custom-alias rules
- Target-URL validation (scheme allowlist, credentials, private hosts, loops)
- Token-bucket rate limiter with bounded memory
- SQLite schema, migrations and link/click repository
- Link service (create/resolve/delete/stats, idempotency, expiry, ownership)
- HTTP API, auth, error mapping, request IDs, health/readiness
