"""Short-code generation and custom-alias rules."""

from __future__ import annotations

import re
import secrets

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
# Paths the service owns; an alias must never shadow them.
RESERVED = frozenset({"api", "healthz", "readyz", "docs", "redoc", "openapi.json", "admin"})


class InvalidAlias(ValueError):
    pass


def generate_code(length: int = 7) -> str:
    """Random base62 code. Random rather than sequential so codes can't be enumerated."""
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def validate_alias(alias: str) -> str:
    if not _ALIAS_RE.fullmatch(alias):
        raise InvalidAlias("alias must be 3-32 characters of letters, digits, '-' or '_'")
    if alias.lower() in RESERVED:
        raise InvalidAlias(f"alias '{alias}' is reserved")
    return alias
