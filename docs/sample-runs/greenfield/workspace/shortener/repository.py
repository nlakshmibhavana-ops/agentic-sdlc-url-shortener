"""Persistence for links and click events."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .db import Database


class DuplicateCode(Exception):
    pass


@dataclass(frozen=True)
class Link:
    code: str
    target_url: str
    owner: str
    created_at: datetime
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    click_count: int = 0


@dataclass(frozen=True)
class Click:
    clicked_at: datetime
    referrer_host: str | None
    user_agent_family: str


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_link(row: sqlite3.Row) -> Link:
    return Link(
        code=row["code"],
        target_url=row["target_url"],
        owner=row["owner"],
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=_dt(row["expires_at"]),
        deleted_at=_dt(row["deleted_at"]),
        click_count=row["click_count"],
    )


class LinkRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def insert(self, link: Link) -> None:
        try:
            with self.db.transaction() as tx:
                tx.execute(
                    "INSERT INTO links (code, target_url, owner, created_at, expires_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        link.code,
                        link.target_url,
                        link.owner,
                        link.created_at.isoformat(),
                        link.expires_at.isoformat() if link.expires_at else None,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateCode(link.code) from exc

    def get(self, code: str) -> Link | None:
        row = self.db.connection().execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()
        return _row_to_link(row) if row else None

    def soft_delete(self, code: str, when: datetime) -> None:
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE links SET deleted_at = ? WHERE code = ? AND deleted_at IS NULL",
                (when.isoformat(), code),
            )

    def record_click(self, code: str, click: Click) -> None:
        # One transaction, and an atomic increment: no lost updates under concurrent clicks.
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO clicks (code, clicked_at, referrer_host, user_agent_family)"
                " VALUES (?, ?, ?, ?)",
                (code, click.clicked_at.isoformat(), click.referrer_host, click.user_agent_family),
            )
            tx.execute("UPDATE links SET click_count = click_count + 1 WHERE code = ?", (code,))

    def daily_clicks(self, code: str, since: datetime) -> list[tuple[str, int]]:
        rows = self.db.connection().execute(
            "SELECT substr(clicked_at, 1, 10) AS day, COUNT(*) AS n FROM clicks"
            " WHERE code = ? AND clicked_at >= ? GROUP BY day ORDER BY day",
            (code, since.isoformat()),
        )
        return [(row["day"], row["n"]) for row in rows]

    def top_referrers(self, code: str, limit: int = 5) -> list[tuple[str, int]]:
        rows = self.db.connection().execute(
            "SELECT COALESCE(referrer_host, 'direct') AS host, COUNT(*) AS n FROM clicks"
            " WHERE code = ? GROUP BY host ORDER BY n DESC, host LIMIT ?",
            (code, limit),
        )
        return [(row["host"], row["n"]) for row in rows]

    def find_idempotent(self, owner: str, key: str) -> tuple[str, str] | None:
        row = (
            self.db.connection()
            .execute(
                "SELECT fingerprint, code FROM idempotency_keys WHERE owner = ? AND key = ?",
                (owner, key),
            )
            .fetchone()
        )
        return (row["fingerprint"], row["code"]) if row else None

    def save_idempotent(
        self, owner: str, key: str, fingerprint: str, code: str, when: datetime
    ) -> None:
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT OR IGNORE INTO idempotency_keys VALUES (?, ?, ?, ?, ?)",
                (owner, key, fingerprint, code, when.isoformat()),
            )
