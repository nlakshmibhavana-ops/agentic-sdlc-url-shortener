# Changelog

## [Unreleased] - Product ask: safer links, marketing analytics, fast redirects

- Q-PERF: bounded TTL read-through cache on the redirect path (p95 < 50 ms)
- Q-SAFETY: configurable domain denylist (incl. subdomains)
- Q-PRIVACY=hashed: unique visitors via daily-rotating HMAC; raw IPs never stored
