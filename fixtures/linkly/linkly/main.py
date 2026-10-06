"""linkly HTTP API."""

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from . import store

DB = os.environ.get("LINKLY_DB", "linkly.db")


class ShortenIn(BaseModel):
    url: str


def create_app(db_path=None):
    db = db_path or DB
    store.init_db(db)
    app = FastAPI(title="linkly")

    @app.post("/shorten")
    def shorten(body: ShortenIn):
        code = store.create(db, body.url)
        return {"code": code, "short": f"/r/{code}"}

    @app.get("/r/{code}")
    def go(code: str):
        row = store.lookup(db, code)
        if not row:
            raise HTTPException(404, "not found")
        store.hit(db, code)
        return RedirectResponse(row["url"], status_code=301)

    @app.get("/info/{code}")
    def info(code: str):
        row = store.lookup(db, code)
        if not row:
            raise HTTPException(404, "not found")
        return {"code": code, "url": row["url"], "clicks": row["clicks"]}

    return app
