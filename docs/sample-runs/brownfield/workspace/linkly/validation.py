"""Target-URL validation (LINK-142).

Only absolute http(s) URLs with a host are accepted. Script-capable schemes such as
javascript: and data: turn a redirect into stored XSS, so they are rejected here,
before anything is written to the database.
"""

from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_URL_LENGTH = 2048


class InvalidURL(ValueError):
    pass


def validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise InvalidURL(f"url must be 1-{MAX_URL_LENGTH} characters")
    if any(ch.isspace() or ord(ch) < 0x20 for ch in url):
        raise InvalidURL("url contains whitespace or control characters")
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidURL("only http and https URLs are allowed")
    if not parts.hostname:
        raise InvalidURL("url must include a host")
    if parts.username or parts.password:
        raise InvalidURL("URLs with embedded credentials are not allowed")
    return url
