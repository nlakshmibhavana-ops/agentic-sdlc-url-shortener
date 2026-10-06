# URL Shortener

A small, production-minded URL shortening service: create short links (random or custom
alias, optional expiry), redirect, and per-link click analytics.

## Run

```bash
pip install -e '.[dev]'
export SHORTENER_API_KEYS=change-me          # comma-separated; management API fails closed without one
python -m shortener                           # http://127.0.0.1:8000, docs at /docs
```

| Variable | Default | Meaning |
|---|---|---|
| `SHORTENER_DB` | `shortener.db` | SQLite file (WAL mode) |
| `SHORTENER_BASE_URL` | `http://localhost:8000` | Prefix for `short_url` |
| `SHORTENER_API_KEYS` | *(none)* | Keys allowed to manage links |
| `SHORTENER_CODE_LENGTH` | `7` | Random code length (62^7 ≈ 3.5e12) |
| `SHORTENER_RATE_LIMIT` | `60` | Creates per minute per API key |
| `SHORTENER_ALLOW_PRIVATE` | off | Allow private/internal targets (intranet deployments) |

## Use

```bash
curl -s -XPOST localhost:8000/api/v1/links -H 'X-API-Key: change-me' \
     -H 'Content-Type: application/json' -d '{"url":"https://example.com/very/long","alias":"demo"}'
curl -si localhost:8000/demo                   # 302 -> https://example.com/very/long
curl -s localhost:8000/api/v1/links/demo/stats -H 'X-API-Key: change-me'
```

See [docs/API.md](docs/API.md) and `openapi.json` for the full contract.

## Design notes

- **Codes** are random base62 (not sequential), so the link space can't be enumerated.
  Collisions are retried; custom aliases are validated and can't shadow service routes.
- **Target validation** allows only http(s), and rejects embedded credentials, private,
  loopback or internal hosts, and links back to the shortener itself.
- **Redirects** are `302` with `Cache-Control: private, no-store`, so every click is
  counted. A `301` would be cached by browsers and under-count.
- **Analytics** are written after the response is sent. They store the referrer *host*
  and a coarse user-agent family only, with no IPs and no full referrer URLs.
- **Ownership**: links belong to the hashed API key that created them. Other keys get
  `404`, not `403`, so codes can't be probed.
- **Reliability**: `Idempotency-Key` makes create safe to retry. A token-bucket rate
  limit returns `429` + `Retry-After`. `/healthz` is liveness, `/readyz` checks the DB.
  Every response carries `X-Request-ID`, and there is one JSON access-log line per request.

## Test

```bash
pytest --cov=shortener
```
