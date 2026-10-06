"""LINK-150: optional link expiry; expired links answer 410 Gone."""

from datetime import UTC, datetime, timedelta

from linkly import store


def test_link_without_expiry_still_works(client):
    code = client.post("/shorten", json={"url": "https://example.com"}).json()["code"]
    assert client.get(f"/r/{code}").status_code == 302
    assert client.get(f"/info/{code}").json()["expires_at"] is None


def test_expiring_link_reports_expiry(client):
    body = client.post("/shorten", json={"url": "https://example.com", "expires_in_days": 7}).json()
    expires = datetime.fromisoformat(client.get(f"/info/{body['code']}").json()["expires_at"])
    assert timedelta(days=6, hours=23) < expires - datetime.now(UTC) <= timedelta(days=7)


def test_expired_link_is_gone(client, db_path):
    code = client.post("/shorten", json={"url": "https://example.com", "expires_in_days": 1}).json()[
        "code"
    ]
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    conn = store.connect(db_path)
    conn.execute("UPDATE urls SET expires_at = ? WHERE code = ?", (past, code))
    conn.commit()
    assert client.get(f"/r/{code}").status_code == 410


def test_expiry_must_be_positive_and_bounded(client):
    for days in (0, -1, 3651):
        resp = client.post("/shorten", json={"url": "https://example.com", "expires_in_days": days})
        assert resp.status_code == 422
