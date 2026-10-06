"""Q-PRIVACY (answer: hashed): unique visitors without storing personal data."""

import sqlite3


def _create(client, auth):
    return client.post("/api/v1/links", json={"url": "https://example.com"}, headers=auth).json()[
        "code"
    ]


def test_unique_visitors_counted_per_day(client, auth, clock):
    code = _create(client, auth)
    alice = {"User-Agent": "Mozilla (X11)"}
    bob = {"User-Agent": "Mozilla (iPhone)"}
    client.get(f"/{code}", headers=alice)
    client.get(f"/{code}", headers=alice)
    client.get(f"/{code}", headers=bob)
    stats = client.get(f"/api/v1/links/{code}/stats", headers=auth).json()
    assert (stats["total_clicks"], stats["unique_visitors"]) == (3, 2)

    clock.advance(86400)  # next day: the daily key rotates, so alice is new again
    client.get(f"/{code}", headers=alice)
    stats = client.get(f"/api/v1/links/{code}/stats", headers=auth).json()
    assert stats["unique_visitors"] == 3


def test_raw_ip_is_never_persisted(client, auth, settings):
    code = _create(client, auth)
    client.get(f"/{code}")  # TestClient connects from host "testclient"
    conn = sqlite3.connect(settings.database_path)
    dump = "\n".join(conn.iterdump())
    assert "testclient" not in dump
    hashes = [r[0] for r in conn.execute("SELECT visitor_hash FROM clicks")]
    assert hashes and all(len(h) == 16 for h in hashes)


def test_existing_database_is_migrated(tmp_path):
    from shortener.db import MIGRATIONS, Database

    path = str(tmp_path / "v1.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    for statement in filter(str.strip, MIGRATIONS[0][1].split(";")):
        conn.execute(statement)
    conn.execute("INSERT INTO schema_migrations VALUES ('0001_initial', 'x')")
    when = "2026-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO links (code, target_url, owner, created_at) VALUES (?, ?, ?, ?)",
        ("old", "https://o.example", "o", when),
    )
    conn.execute("INSERT INTO clicks (code, clicked_at) VALUES (?, ?)", ("old", when))
    conn.commit()
    conn.close()
    assert Database(path).migrate() == ["0002_visitor_hash"]
    row = sqlite3.connect(path).execute("SELECT code, visitor_hash FROM clicks").fetchone()
    assert row == ("old", None)
