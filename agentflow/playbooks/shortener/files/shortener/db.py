"""SQLite access: per-thread connections, WAL mode and forward-only migrations."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager

# Append-only. Never edit a migration that has shipped; add a new one.
MIGRATIONS: list[tuple[str, str]] = [
    (
        "0001_initial",
        """
        CREATE TABLE links (
            code         TEXT PRIMARY KEY,
            target_url   TEXT NOT NULL,
            owner        TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            expires_at   TEXT,
            deleted_at   TEXT,
            click_count  INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX idx_links_owner ON links(owner);
        CREATE TABLE clicks (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            code               TEXT NOT NULL REFERENCES links(code),
            clicked_at         TEXT NOT NULL,
            referrer_host      TEXT,
            user_agent_family  TEXT
        );
        CREATE INDEX idx_clicks_code_time ON clicks(code, clicked_at);
        CREATE TABLE idempotency_keys (
            owner        TEXT NOT NULL,
            key          TEXT NOT NULL,
            fingerprint  TEXT NOT NULL,
            code         TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            PRIMARY KEY (owner, key)
        );
        """,
    ),
]


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._local = threading.local()

    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def migrate(self) -> list[str]:
        """Apply pending migrations; returns the names applied."""
        conn = self.connection()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT)"
        )
        done = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}
        applied = []
        for name, sql in MIGRATIONS:
            if name in done:
                continue
            with self.transaction() as tx:
                for statement in filter(str.strip, sql.split(";")):
                    tx.execute(statement)
                tx.execute("INSERT INTO schema_migrations VALUES (?, datetime('now'))", (name,))
            applied.append(name)
        return applied

    def ping(self) -> bool:
        try:
            self.connection().execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return False
        return True
