"""linkly HTTP API."""

import os
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from . import store
from .validation import InvalidURL, validate_url

DB = os.environ.get("LINKLY_DB", "linkly.db")


class ShortenIn(BaseModel):
    url: str
    expires_in_days: int | None = Field(None, ge=1, le=3650)


def create_app(db_path=None):
    db = db_path or DB
    store.init_db(db)
    app = FastAPI(title="linkly")

    @app.post("/shorten")
    def shorten(body: ShortenIn):
        try:
            url = validate_url(body.url)
        except InvalidURL as exc:
            raise HTTPException(400, str(exc)) from exc
        expires_at = None
        if body.expires_in_days is not None:
            expires_at = (
                datetime.now(UTC) + timedelta(days=body.expires_in_days)
            ).isoformat()
        code = store.create(db, url, expires_at)
        return {"code": code, "short": f"/r/{code}"}

    @app.get("/r/{code}")
    def go(code: str):
        row = store.lookup(db, code)
        if not row:
            raise HTTPException(404, "not found")
        if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            raise HTTPException(410, "link expired")
        store.hit(db, code)
        # 302 + no-store: a cached 301 never reaches us again, so clicks went uncounted.
        return RedirectResponse(
            row["url"], status_code=302, headers={"Cache-Control": "private, no-store"}
        )

    @app.get("/info/{code}")
    def info(code: str):
        row = store.lookup(db, code)
        if not row:
            raise HTTPException(404, "not found")
        return {
            "code": code,
            "url": row["url"],
            "clicks": row["clicks"],
            "expires_at": row["expires_at"],
        }

    return app
