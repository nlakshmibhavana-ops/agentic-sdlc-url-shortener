# Design record

Architecture: modular monolith: api -> service -> repository -> db, pure helpers below

## Capabilities

- **analytics**: Per-link total clicks, daily breakdown and top referrer hosts; Click recording never delays the redirect response
- **domain_denylist**: Targets on a configurable domain denylist (incl. subdomains) are rejected with 400
- **performance**: Redirect path meets the agreed latency target
- **redirect**: GET /{code} returns 302 to the target; Unknown codes return 404; deleted or expired codes return 410
- **redirect_cache**: Hot redirects are served from a bounded in-process TTL cache; Deleting a link invalidates its cache entry immediately; Redirect p95 < 50 ms in-process (perf smoke test)
- **unique_visitors**: Stats report unique visitors per link; No raw IP address is ever persisted: visitors are a keyed, daily-rotating hash
- **url_safety**: Only http(s) targets are accepted; script/data/file schemes are rejected with 400; URLs with embedded credentials are rejected

## Decisions (ADRs)

### ADR-01: Record clicks after the response; store referrer host + UA family only

Keeps redirect latency independent of analytics writes and minimises personal data.

Alternatives considered: Synchronous write (simpler, slower); Event queue (scales, more infrastructure)

### ADR-02: Configurable domain denylist, matched on registrable suffix

Cheap, deterministic, auditable; reputation APIs add a vendor dependency.

Alternatives considered: Google Safe Browsing lookup (external dependency, latency)

### ADR-03: 302 + Cache-Control: no-store for redirects

Every click must reach the service for analytics; 301 is cached by browsers.

Alternatives considered: 301 Moved Permanently (cheaper, but under-counts)

### ADR-04: Bounded in-process TTL cache on the redirect path

Meets p95 < 50 ms with no new infrastructure; TTL bounds staleness, delete invalidates.

Alternatives considered: Redis/CDN edge cache (needed only for p95 < 10 ms or multi-instance)

### ADR-05: Unique visitors via HMAC(secret, day | IP | UA), truncated

Counts uniques per day without storing personal data; the daily key prevents tracking a visitor across days.

Alternatives considered: Store raw IPs (personal data, needs legal basis); Cookies (consent banner needed)

### ADR-06: Validate targets at the boundary; never fetch them

Blocks script schemes, credential spoofing and internal hosts without network I/O.

Alternatives considered: Fetch-and-inspect targets (SSRF risk, latency)

## Assumptions

- Q-PERF: assumed 'p95 < 50 ms in-process (read-through cache, no new infra)' (non-blocking; confirm or override)
- Q-SAFETY: assumed 'Add a configurable domain denylist for known-bad domains' (non-blocking; confirm or override)
