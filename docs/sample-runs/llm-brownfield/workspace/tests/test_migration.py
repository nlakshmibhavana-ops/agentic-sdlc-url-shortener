"""Existing production databases (created by v0.4.x) must upgrade in place without data loss."""

import sqlite3

from linkly import store

V04_SCHEMA = (
    "CREATE TABLE urls (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE,"
    " url TEXT NOT NULL, clicks INTEGER NOT NULL DEFAULT 0,"
    " created TEXT DEFAULT CURRENT_TIMESTAMP)"
)


def test_upgrade_existing_database(tmp_path):
    path = str(tmp_path / "prod-v04.db")
    conn = sqlite3.connect(path)
    conn.execute(V04_SCHEMA)
    conn.execute("INSERT INTO urls (code, url, clicks) VALUES ('1', 'https://old.example', 41)")
    conn.commit()
    conn.close()

    store.init_db(path)
    store.init_db(path)  # idempotent

    row = store.lookup(path, "1")
    assert row["url"] == "https://old.example"
    assert row["clicks"] == 41
    assert row["expires_at"] is None
