"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    database_path: str = "shortener.db"
    base_url: str = "http://localhost:8000"
    # Management endpoints fail closed: with no keys configured, every call is 401.
    api_keys: tuple[str, ...] = ()
    code_length: int = 7
    max_url_length: int = 2048
    rate_limit_per_minute: int = 60
    allow_private_targets: bool = False
    # Key for visitor hashes. Unset: a random per-process key (uniques reset on restart).
    analytics_key: str = ""
    blocked_domains: tuple[str, ...] = ()
    redirect_cache_size: int = 10_000
    redirect_cache_ttl_s: float = 30.0  # bounds staleness across instances

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            database_path=env.get("SHORTENER_DB", cls.database_path),
            base_url=env.get("SHORTENER_BASE_URL", cls.base_url).rstrip("/"),
            api_keys=_csv(env.get("SHORTENER_API_KEYS", "")),
            code_length=int(env.get("SHORTENER_CODE_LENGTH", cls.code_length)),
            rate_limit_per_minute=int(env.get("SHORTENER_RATE_LIMIT", cls.rate_limit_per_minute)),
            allow_private_targets=env.get("SHORTENER_ALLOW_PRIVATE", "") == "1",
            analytics_key=env.get("SHORTENER_ANALYTICS_KEY", ""),
            blocked_domains=_csv(env.get("SHORTENER_BLOCKED_DOMAINS", "").lower()),
            redirect_cache_ttl_s=float(env.get("SHORTENER_CACHE_TTL", cls.redirect_cache_ttl_s)),
        )
