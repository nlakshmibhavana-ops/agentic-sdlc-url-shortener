"""Q-PERF: agreed objective is redirect p95 < 50 ms in-process."""

import time


def test_redirect_p95_under_50ms(client, auth):
    code = client.post("/api/v1/links", json={"url": "https://example.com"}, headers=auth).json()[
        "code"
    ]
    timings = []
    for _ in range(200):
        started = time.perf_counter()
        assert client.get(f"/{code}").status_code == 302
        timings.append(time.perf_counter() - started)
    p95 = sorted(timings)[int(len(timings) * 0.95) - 1]
    assert p95 < 0.050, f"p95 {p95 * 1000:.1f} ms"


def test_delete_invalidates_cached_redirect(client, auth):
    code = client.post("/api/v1/links", json={"url": "https://example.com"}, headers=auth).json()[
        "code"
    ]
    assert client.get(f"/{code}").status_code == 302  # warms the cache
    assert client.delete(f"/api/v1/links/{code}", headers=auth).status_code == 204
    assert client.get(f"/{code}").status_code == 410


def test_cached_link_still_expires(client, auth, clock):
    from datetime import timedelta

    expires = (clock.now + timedelta(seconds=10)).isoformat()
    body = {"url": "https://example.com", "expires_at": expires}
    code = client.post("/api/v1/links", json=body, headers=auth).json()["code"]
    assert client.get(f"/{code}").status_code == 302
    clock.advance(11)
    assert client.get(f"/{code}").status_code == 410
