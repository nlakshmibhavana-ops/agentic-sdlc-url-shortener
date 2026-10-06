"""SQLite storage for linkly."""

import sqlite3
import string
import threading

ALPHABET = string.digits + string.ascii_letters

_local = threading.local()


def connect(path):
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != path:
        conn = sqlite3.connect(path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        _local.conn, _local.path = conn, path
    return conn


def init_db(path):
    conn = connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS urls ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " code TEXT UNIQUE,"
        " url TEXT NOT NULL,"
        " clicks INTEGER NOT NULL DEFAULT 0,"
    " expires_at TEXT,"
        " created TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(urls)")}
    if "expires_at" not in columns:
        # v0.5 migration: additive and nullable, so v0.4 rows keep working unchanged.
        conn.execute("ALTER TABLE urls ADD COLUMN expires_at TEXT")
    conn.commit()


def encode(n):
    out = ""
    while n:
        n, r = divmod(n, 62)
        out = ALPHABET[r] + out
    return out or "0"


def create(path, url, expires_at=None):
    conn = connect(path)
    cur = conn.execute(
        "INSERT INTO urls (url, expires_at) VALUES (?, ?)", (url, expires_at)
    )
    code = encode(cur.lastrowid)
    conn.execute("UPDATE urls SET code = ? WHERE id = ?", (code, cur.lastrowid))
    conn.commit()
    return code


def lookup(path, code):
    row = connect(path).execute("SELECT * FROM urls WHERE code = ?", (code,)).fetchone()
    return dict(row) if row else None


def hit(path, code):
    # Single atomic statement: the old read-modify-write lost updates under load.
    conn = connect(path)
    conn.execute("UPDATE urls SET clicks = clicks + 1 WHERE code = ?", (code,))
    conn.commit()
