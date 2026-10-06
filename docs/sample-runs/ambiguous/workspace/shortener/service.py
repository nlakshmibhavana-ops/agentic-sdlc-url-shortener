"""Business rules for creating, resolving and reporting on links."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from .cache import TTLCache
from .codes import generate_code, validate_alias
from .config import Settings
from .repository import Click, DuplicateCode, Link, LinkRepository
from .validation import validate_target_url

MAX_CODE_ATTEMPTS = 5


class LinkNotFound(Exception):
    pass


class LinkGone(Exception):
    pass


class AliasTaken(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


class InvalidExpiry(ValueError):
    pass


@dataclass(frozen=True)
class LinkStats:
    code: str
    total_clicks: int
    daily: list[tuple[str, int]]
    top_referrers: list[tuple[str, int]]
    unique_visitors: int = 0


def utcnow() -> datetime:
    return datetime.now(UTC)


def owner_id(api_key: str) -> str:
    """Stable, non-reversible owner id so raw API keys never reach the database."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def user_agent_family(user_agent: str | None) -> str:
    ua = (user_agent or "").lower()
    if not ua:
        return "unknown"
    if any(token in ua for token in ("bot", "crawler", "spider", "preview")):
        return "bot"
    if any(token in ua for token in ("mobile", "android", "iphone")):
        return "mobile"
    return "desktop"


class LinkService:
    def __init__(
        self,
        repo: LinkRepository,
        settings: Settings,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.repo = repo
        self.settings = settings
        self.clock = clock
        self._visitor_key = (settings.analytics_key or secrets.token_hex(32)).encode()
        self.cache: TTLCache[Link] = TTLCache(
            settings.redirect_cache_size, settings.redirect_cache_ttl_s
        )

    def create(
        self,
        url: str,
        owner: str,
        alias: str | None = None,
        expires_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[Link, bool]:
        """Create a link. Returns (link, created); created is False on an idempotent replay."""
        target = validate_target_url(
            url,
            max_length=self.settings.max_url_length,
            base_url=self.settings.base_url,
            allow_private=self.settings.allow_private_targets,
            blocked_domains=self.settings.blocked_domains,
        )
        now = self.clock()
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                raise InvalidExpiry("expires_at must be in the future")

        fingerprint = hashlib.sha256(
            f"{target}|{alias}|{expires_at.isoformat() if expires_at else ''}".encode()
        ).hexdigest()
        if idempotency_key:
            previous = self.repo.find_idempotent(owner, idempotency_key)
            if previous is not None:
                if previous[0] != fingerprint:
                    raise IdempotencyConflict("Idempotency-Key reused with a different request")
                link = self.repo.get(previous[1])
                if link is not None:
                    return link, False

        if alias is not None:
            link = Link(validate_alias(alias), target, owner, now, expires_at)
            try:
                self.repo.insert(link)
            except DuplicateCode as exc:
                raise AliasTaken(alias) from exc
        else:
            for _ in range(MAX_CODE_ATTEMPTS):
                link = Link(
                    generate_code(self.settings.code_length), target, owner, now, expires_at
                )
                try:
                    self.repo.insert(link)
                    break
                except DuplicateCode:
                    continue
            else:
                raise RuntimeError("could not allocate a unique code; increase code length")

        if idempotency_key:
            self.repo.save_idempotent(owner, idempotency_key, fingerprint, link.code, now)
        return link, True

    def resolve(self, code: str) -> Link:
        """Look up a link for redirecting. Raises LinkNotFound or LinkGone."""
        link = self.cache.get(code)
        if link is None:
            link = self.repo.get(code)
            if link is None:  # misses are not cached: a new alias must work at once
                raise LinkNotFound(code)
            self.cache.set(code, link)
        # Expiry/deletion are still checked on every hit, cached or not.
        if link.deleted_at is not None or (
            link.expires_at is not None and link.expires_at <= self.clock()
        ):
            raise LinkGone(code)
        return link

    def get_owned(self, code: str, owner: str) -> Link:
        link = self.repo.get(code)
        # Someone else's link is reported as missing so codes can't be probed.
        if link is None or link.owner != owner:
            raise LinkNotFound(code)
        return link

    def delete(self, code: str, owner: str) -> None:
        self.get_owned(code, owner)
        self.repo.soft_delete(code, self.clock())
        self.cache.invalidate(code)

    def visitor_hash(self, client_ip: str | None, user_agent: str | None) -> str | None:
        """Keyed hash of (day, IP, UA). The raw IP never leaves this function."""
        if not client_ip:
            return None
        message = f"{self.clock().date().isoformat()}|{client_ip}|{user_agent or ''}"
        return hmac.new(self._visitor_key, message.encode(), hashlib.sha256).hexdigest()[:16]

    def record_click(
        self,
        code: str,
        referrer: str | None,
        user_agent: str | None,
        client_ip: str | None = None,
    ) -> None:
        referrer_host = urlsplit(referrer).hostname if referrer else None
        click = Click(
            self.clock(),
            referrer_host,
            user_agent_family(user_agent),
            self.visitor_hash(client_ip, user_agent),
        )
        self.repo.record_click(code, click)

    def stats(self, code: str, owner: str, days: int = 30) -> LinkStats:
        link = self.get_owned(code, owner)
        since = self.clock() - timedelta(days=days)
        return LinkStats(
            code=link.code,
            total_clicks=link.click_count,
            daily=self.repo.daily_clicks(code, since),
            top_referrers=self.repo.top_referrers(code),
            unique_visitors=self.repo.unique_visitors(code, since),
        )
