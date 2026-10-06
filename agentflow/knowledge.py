"""Domain knowledge the deterministic agents reason with.

In LLM mode the model replaces the keyword matching, but its output is still validated
against this vocabulary: playbooks, gates and traceability key off capability ids, so an
unknown capability is rejected rather than silently ignored.
"""

from __future__ import annotations

CAPABILITIES: dict[str, dict] = {
    "platform": {
        "keywords": [],
        "criteria": ["Service starts from a single command with documented configuration"],
    },
    "link_creation": {
        "keywords": ["shorten", "short link", "short url", "create link", "create short"],
        "criteria": [
            "POST /api/v1/links returns 201 with code and short_url",
            "Generated codes are random base62, not sequential or enumerable",
            "Code collisions are retried transparently",
        ],
    },
    "redirect": {
        "keywords": ["redirect"],
        "criteria": [
            "GET /{code} returns 302 to the target",
            "Unknown codes return 404; deleted or expired codes return 410",
        ],
    },
    "custom_alias": {
        "keywords": ["alias", "vanity", "custom code"],
        "criteria": [
            "Aliases are 3-32 chars [A-Za-z0-9_-] and cannot shadow service routes",
            "A taken alias returns 409",
        ],
    },
    "expiry": {
        "keywords": ["expire", "expiry", "expiration", "ttl"],
        "criteria": [
            "Links may carry an optional expiry in the future",
            "Expired links return 410 Gone",
        ],
    },
    "analytics": {
        "keywords": ["analytics", "click", "stats", "statistic", "perform", "track"],
        "criteria": [
            "Per-link total clicks, daily breakdown and top referrer hosts",
            "Click recording never delays the redirect response",
        ],
    },
    "rate_limiting": {
        "keywords": ["rate limit", "rate-limit", "abuse", "throttl"],
        "criteria": ["Link creation is limited per API key; excess returns 429 + Retry-After"],
    },
    "url_safety": {
        "keywords": [
            "javascript:",
            "unsafe url",
            "malicious",
            "phishing",
            "xss",
            "safer",
            "secure",
            "safety",
        ],
        "criteria": [
            "Only http(s) targets are accepted; script/data/file schemes are rejected with 400",
            "URLs with embedded credentials are rejected",
        ],
    },
    "click_accuracy": {
        "keywords": ["under-report", "underreport", "lost update", "click counts are"],
        "criteria": [
            "Concurrent clicks are all counted (atomic increment)",
            "Redirects are not permanently cacheable, so repeat clicks reach the service",
        ],
    },
    "reliability": {
        "keywords": ["idempot", "health", "readiness", "retry", "reliab"],
        "criteria": [
            "Create is safe to retry with Idempotency-Key",
            "/healthz (liveness) and /readyz (DB check) endpoints",
            "Every response carries X-Request-ID",
        ],
    },
    "performance": {
        "keywords": ["fast", "latency", "performance", "quick", "speed"],
        "criteria": ["Redirect path meets the agreed latency target"],
    },
    # Capabilities that only arise from answers to clarifying questions.
    "redirect_cache": {
        "keywords": [],
        "criteria": [
            "Hot redirects are served from a bounded in-process TTL cache",
            "Deleting a link invalidates its cache entry immediately",
            "Redirect p95 < 50 ms in-process (perf smoke test)",
        ],
    },
    "domain_denylist": {
        "keywords": [],
        "criteria": [
            "Targets on a configurable domain denylist (incl. subdomains) are rejected with 400",
        ],
    },
    "unique_visitors": {
        "keywords": [],
        "criteria": [
            "Stats report unique visitors per link",
            "No raw IP address is ever persisted: visitors are a keyed, daily-rotating hash",
        ],
    },
    "unique_visitors_raw": {
        "keywords": [],
        "criteria": ["Stats report unique visitors using stored client IPs"],
    },
}

# A specific capability in a sentence means the sentence is about it, not the generic
# feature it mentions in passing ("POST /shorten accepts javascript: URLs" is a safety
# defect, not a request to build link creation).
REFINES: dict[str, list[str]] = {
    "click_accuracy": ["analytics"],
    "url_safety": ["link_creation"],
}

DESIGN: dict[str, dict] = {
    "link_creation": {
        "components": ["codes (generator)", "service.LinkService", "repository.LinkRepository"],
        "endpoints": ["POST /api/v1/links", "GET /api/v1/links/{code}", "DELETE /api/v1/links/{code}"],
        "adr": (
            "Random base62 codes (7 chars) with collision retry",
            "62^7 ≈ 3.5e12 codes; random codes cannot be enumerated, unlike sequential ids.",
            ["Sequential id + base62 (enumerable)", "Hash of URL (dedup, but leaks equality)"],
        ),
    },
    "redirect": {
        "components": ["api.follow"],
        "endpoints": ["GET /{code}"],
        "adr": (
            "302 + Cache-Control: no-store for redirects",
            "Every click must reach the service for analytics; 301 is cached by browsers.",
            ["301 Moved Permanently (cheaper, but under-counts)"],
        ),
    },
    "analytics": {
        "components": ["clicks table", "service.stats", "BackgroundTasks click recorder"],
        "endpoints": ["GET /api/v1/links/{code}/stats"],
        "adr": (
            "Record clicks after the response; store referrer host + UA family only",
            "Keeps redirect latency independent of analytics writes and minimises personal data.",
            ["Synchronous write (simpler, slower)", "Event queue (scales, more infrastructure)"],
        ),
    },
    "rate_limiting": {
        "components": ["ratelimit.RateLimiter (token bucket)"],
        "endpoints": [],
        "adr": (
            "In-process token bucket keyed by API-key owner",
            "Zero infrastructure for a single instance; swap for Redis when horizontally scaled.",
            ["Redis sliding window (multi-instance)", "API gateway limits"],
        ),
    },
    "url_safety": {
        "components": ["validation.validate_target_url"],
        "endpoints": [],
        "adr": (
            "Validate targets at the boundary; never fetch them",
            "Blocks script schemes, credential spoofing and internal hosts without network I/O.",
            ["Fetch-and-inspect targets (SSRF risk, latency)"],
        ),
    },
    "expiry": {
        "components": ["links.expires_at", "service.resolve"],
        "endpoints": [],
        "adr": (
            "Expiry evaluated at read time; 410 Gone",
            "No background job required; expired rows remain for audit/analytics.",
            ["Background purge job"],
        ),
    },
    "reliability": {
        "components": ["idempotency_keys table", "/healthz", "/readyz", "request-id middleware"],
        "endpoints": ["GET /healthz", "GET /readyz"],
        "adr": (
            "SQLite (WAL) for the prototype behind a repository interface",
            "Single-file, zero-ops, transactional; the repository boundary allows Postgres later.",
            ["PostgreSQL now (more ops for a prototype)", "Redis (weak durability)"],
        ),
    },
    "custom_alias": {"components": ["codes.validate_alias"], "endpoints": [], "adr": None},
    "platform": {"components": ["config.Settings", "__main__"], "endpoints": [], "adr": None},
    "click_accuracy": {
        "components": ["store.hit", "main.go"],
        "endpoints": ["GET /r/{code}"],
        "adr": (
            "Atomic UPDATE ... SET clicks = clicks + 1 and 302 redirects",
            "Fixes lost updates under concurrency and browser-cached 301s that never re-hit the service.",
            ["Row locking with SELECT ... FOR UPDATE (not in SQLite)"],
        ),
    },
    "redirect_cache": {
        "components": ["cache.TTLCache", "service.resolve (read-through)"],
        "endpoints": [],
        "adr": (
            "Bounded in-process TTL cache on the redirect path",
            "Meets p95 < 50 ms with no new infrastructure; TTL bounds staleness, delete invalidates.",
            ["Redis/CDN edge cache (needed only for p95 < 10 ms or multi-instance)"],
        ),
    },
    "domain_denylist": {
        "components": ["validation (denylist)", "config.blocked_domains"],
        "endpoints": [],
        "adr": (
            "Configurable domain denylist, matched on registrable suffix",
            "Cheap, deterministic, auditable; reputation APIs add a vendor dependency.",
            ["Google Safe Browsing lookup (external dependency, latency)"],
        ),
    },
    "unique_visitors": {
        "components": ["clicks.visitor_hash", "migration 0002"],
        "endpoints": ["GET /api/v1/links/{code}/stats"],
        "adr": (
            "Unique visitors via HMAC(secret, day | IP | UA), truncated",
            "Counts uniques per day without storing personal data; the daily key prevents tracking "
            "a visitor across days.",
            ["Store raw IPs (personal data, needs legal basis)", "Cookies (consent banner needed)"],
        ),
    },
}

# Ambiguity rules: a vague term in a sentence with no measurable target raises a question.
QUESTIONS: list[dict] = [
    {
        "id": "Q-PRIVACY",
        "triggers": ["analytics", "how links perform", "track", "visitors", "who clicked"],
        "unless": ["no ip", "personal data", "pii", "privacy"],
        "requires_capability": "analytics",
        "text": "Marketing will likely want unique visitors, which needs a per-visitor identifier "
        "(IP address). What may we store?",
        "blocking": True,  # personal data: never assumed, always a human decision
        "options": {
            "hashed": {
                "label": "Daily-rotating keyed hash of IP+UA; raw IP never stored",
                "adds": ["unique_visitors"],
            },
            "raw": {"label": "Store raw client IPs", "adds": ["unique_visitors_raw"]},
            "none": {"label": "Total clicks only; no unique visitors", "adds": []},
        },
        "default": None,
    },
    {
        "id": "Q-PERF",
        "triggers": ["fast", "faster", "quick", "performance", "speed"],
        "requires_capability": None,
        "text": "'Fast' has no target. Which latency objective should the redirect path meet?",
        "blocking": False,
        "options": {
            "p95-50ms": {
                "label": "p95 < 50 ms in-process (read-through cache, no new infra)",
                "adds": ["redirect_cache"],
            },
            "p95-10ms": {
                "label": "p95 < 10 ms globally (needs Redis/CDN edge: infra change)",
                "adds": ["redirect_cache"],
                "out_of_scope": "edge caching / CDN",
            },
        },
        "default": "p95-50ms",
    },
    {
        "id": "Q-SAFETY",
        "triggers": ["safer", "safe", "secure", "security"],
        "requires_capability": None,
        "text": "'Safer' could mean several threat models. Which should this change cover?",
        "blocking": False,
        "options": {
            "baseline": {"label": "Keep existing scheme/credential/private-host validation only", "adds": []},
            "denylist": {
                "label": "Add a configurable domain denylist for known-bad domains",
                "adds": ["domain_denylist"],
            },
            "reputation": {
                "label": "Check targets against a reputation API (Safe Browsing)",
                "adds": ["domain_denylist"],
                "out_of_scope": "third-party reputation lookups",
            },
        },
        "default": "denylist",
    },
]
