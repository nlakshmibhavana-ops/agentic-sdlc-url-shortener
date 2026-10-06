# Release 1.1.0

Product ask: safer links, marketing analytics, fast redirects

## Changes

- Q-PERF: bounded TTL read-through cache on the redirect path (p95 < 50 ms)
- Q-SAFETY: configurable domain denylist (incl. subdomains)
- Q-PRIVACY=hashed: unique visitors via daily-rotating HMAC; raw IPs never stored

## Evidence

- [x] all tests pass: 79 passed / 0 failed
- [x] coverage >= 90%: 96.9%
- [x] lint clean: 0 issues
- [x] no security findings: 0 findings
- [x] docs generated: openapi.json, docs/API.md, docs/DESIGN.md, CHANGELOG.md
- [x] no blocking questions open: []

## Known risks / follow-ups

- assumption: Q-PERF: assumed 'p95 < 50 ms in-process (read-through cache, no new infra)' (non-blocking; confirm or override)
- assumption: Q-SAFETY: assumed 'Add a configurable domain denylist for known-bad domains' (non-blocking; confirm or override)
