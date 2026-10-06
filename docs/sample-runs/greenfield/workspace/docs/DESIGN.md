# Design record

Architecture: modular monolith: api -> service -> repository -> db, pure helpers below

## Capabilities

- **analytics**: Per-link total clicks, daily breakdown and top referrer hosts; Click recording never delays the redirect response
- **custom_alias**: Aliases are 3-32 chars [A-Za-z0-9_-] and cannot shadow service routes; A taken alias returns 409
- **expiry**: Links may carry an optional expiry in the future; Expired links return 410 Gone
- **link_creation**: POST /api/v1/links returns 201 with code and short_url; Generated codes are random base62, not sequential or enumerable; Code collisions are retried transparently
- **rate_limiting**: Link creation is limited per API key; excess returns 429 + Retry-After
- **redirect**: GET /{code} returns 302 to the target; Unknown codes return 404; deleted or expired codes return 410
- **reliability**: Create is safe to retry with Idempotency-Key; /healthz (liveness) and /readyz (DB check) endpoints; Every response carries X-Request-ID
- **url_safety**: Only http(s) targets are accepted; script/data/file schemes are rejected with 400; URLs with embedded credentials are rejected

## Decisions (ADRs)

### ADR-01: Record clicks after the response; store referrer host + UA family only

Keeps redirect latency independent of analytics writes and minimises personal data.

Alternatives considered: Synchronous write (simpler, slower); Event queue (scales, more infrastructure)

### ADR-02: Expiry evaluated at read time; 410 Gone

No background job required; expired rows remain for audit/analytics.

Alternatives considered: Background purge job

### ADR-03: Random base62 codes (7 chars) with collision retry

62^7 ≈ 3.5e12 codes; random codes cannot be enumerated, unlike sequential ids.

Alternatives considered: Sequential id + base62 (enumerable); Hash of URL (dedup, but leaks equality)

### ADR-04: In-process token bucket keyed by API-key owner

Zero infrastructure for a single instance; swap for Redis when horizontally scaled.

Alternatives considered: Redis sliding window (multi-instance); API gateway limits

### ADR-05: 302 + Cache-Control: no-store for redirects

Every click must reach the service for analytics; 301 is cached by browsers.

Alternatives considered: 301 Moved Permanently (cheaper, but under-counts)

### ADR-06: SQLite (WAL) for the prototype behind a repository interface

Single-file, zero-ops, transactional; the repository boundary allows Postgres later.

Alternatives considered: PostgreSQL now (more ops for a prototype); Redis (weak durability)

### ADR-07: Validate targets at the boundary; never fetch them

Blocks script schemes, credential spoofing and internal hosts without network I/O.

Alternatives considered: Fetch-and-inspect targets (SSRF risk, latency)
