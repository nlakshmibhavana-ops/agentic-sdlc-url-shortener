# Changelog

## [Unreleased] - linkly v0.4 -> v0.5: security fix, click accuracy fix, link expiry

- Author regression tests for LINK-142/150/151 before touching code (test-first)
- LINK-142: reject script/data/credential URLs at the API boundary (extract validation module)
- LINK-151: atomic click increment; 302 + no-store so browsers stop caching redirects
- LINK-150: optional expires_in_days; 410 Gone after expiry; in-place schema migration
