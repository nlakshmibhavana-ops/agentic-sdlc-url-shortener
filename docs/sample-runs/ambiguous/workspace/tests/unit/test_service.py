from datetime import timedelta

import pytest

from shortener.config import Settings
from shortener.db import Database
from shortener.repository import LinkRepository
from shortener.service import (
    AliasTaken,
    IdempotencyConflict,
    InvalidExpiry,
    LinkGone,
    LinkNotFound,
    LinkService,
    user_agent_family,
)


@pytest.fixture
def service(tmp_path, clock):
    db = Database(str(tmp_path / "svc.db"))
    db.migrate()
    return LinkService(LinkRepository(db), Settings(base_url="http://sho.rt"), clock)


def test_migrations_are_idempotent(tmp_path):
    from shortener.db import MIGRATIONS

    db = Database(str(tmp_path / "m.db"))
    assert db.migrate() == [name for name, _ in MIGRATIONS]
    assert db.migrate() == []


def test_create_and_resolve(service):
    link, created = service.create("https://example.com", "owner")
    assert created
    assert service.resolve(link.code).target_url == "https://example.com"


def test_alias_conflict(service):
    service.create("https://example.com", "owner", alias="promo")
    with pytest.raises(AliasTaken):
        service.create("https://example.org", "other", alias="promo")


def test_retries_code_collisions(service, monkeypatch):
    codes = iter(["AAAAAAA", "AAAAAAA", "BBBBBBB"])
    monkeypatch.setattr("shortener.service.generate_code", lambda n: next(codes))
    first, _ = service.create("https://example.com/1", "owner")
    second, _ = service.create("https://example.com/2", "owner")
    assert (first.code, second.code) == ("AAAAAAA", "BBBBBBB")


def test_expiry_must_be_future_and_is_enforced(service, clock):
    with pytest.raises(InvalidExpiry):
        service.create("https://example.com", "owner", expires_at=clock.now)
    link, _ = service.create(
        "https://example.com", "owner", expires_at=clock.now + timedelta(hours=1)
    )
    clock.advance(3601)
    with pytest.raises(LinkGone):
        service.resolve(link.code)


def test_idempotency_replay_and_conflict(service):
    a, created_a = service.create("https://example.com", "owner", idempotency_key="k1")
    b, created_b = service.create("https://example.com", "owner", idempotency_key="k1")
    assert (a.code, created_a, created_b) == (b.code, True, False)
    with pytest.raises(IdempotencyConflict):
        service.create("https://example.org", "owner", idempotency_key="k1")


def test_owner_isolation(service):
    link, _ = service.create("https://example.com", "owner")
    with pytest.raises(LinkNotFound):
        service.get_owned(link.code, "intruder")
    with pytest.raises(LinkNotFound):
        service.delete(link.code, "intruder")


def test_click_counting_is_atomic_under_concurrency(service):
    from concurrent.futures import ThreadPoolExecutor

    link, _ = service.create("https://example.com", "owner")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: service.record_click(link.code, None, "x"), range(80)))
    assert service.stats(link.code, "owner").total_clicks == 80


@pytest.mark.parametrize(
    "ua,family",
    [
        (None, "unknown"),
        ("Googlebot/2.1", "bot"),
        ("Mozilla (iPhone)", "mobile"),
        ("Mozilla (X11)", "desktop"),
    ],
)
def test_user_agent_family(ua, family):
    assert user_agent_family(ua) == family
