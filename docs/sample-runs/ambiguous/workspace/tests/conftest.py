from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

API_KEY = "test-key-1"
OTHER_KEY = "test-key-2"


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        self.mono = 1000.0

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.mono += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings(tmp_path):
    from shortener.config import Settings

    return Settings(
        database_path=str(tmp_path / "test.db"),
        base_url="http://sho.rt",
        api_keys=(API_KEY, OTHER_KEY),
        rate_limit_per_minute=5,
    )


@pytest.fixture
def client(settings, clock):
    # Imported lazily so module-level unit tests can run before the API layer exists.
    from fastapi.testclient import TestClient

    from shortener.api import create_app

    app = create_app(settings, clock=clock, limiter_clock=clock.monotonic)
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


@pytest.fixture
def other_auth() -> dict[str, str]:
    return {"X-API-Key": OTHER_KEY}
