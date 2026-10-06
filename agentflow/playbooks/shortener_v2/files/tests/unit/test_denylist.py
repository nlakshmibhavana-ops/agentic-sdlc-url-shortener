"""Q-SAFETY: configurable domain denylist (including subdomains)."""

import pytest

from shortener.validation import InvalidURL, validate_target_url

BLOCKED = ("evil.example", "phish.test")


def check(url):
    return validate_target_url(
        url, max_length=2048, base_url="http://sho.rt", blocked_domains=BLOCKED
    )


@pytest.mark.parametrize(
    "url", ["https://evil.example/x", "https://login.evil.example/", "http://PHISH.test"]
)
def test_blocked_domains_and_subdomains(url):
    with pytest.raises(InvalidURL, match="blocked"):
        check(url)


@pytest.mark.parametrize("url", ["https://notevil.example/", "https://evil.example.com/"])
def test_similar_but_different_domains_allowed(url):
    assert check(url)


def test_denylist_applies_through_the_service(tmp_path):
    from shortener.config import Settings
    from shortener.db import Database
    from shortener.repository import LinkRepository
    from shortener.service import LinkService

    db = Database(str(tmp_path / "d.db"))
    db.migrate()
    service = LinkService(LinkRepository(db), Settings(blocked_domains=BLOCKED))
    with pytest.raises(InvalidURL):
        service.create("https://evil.example/", "owner")
