"""HTTP layer: routing, auth, rate limiting and error mapping."""

import hmac
import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import __version__
from .codes import InvalidAlias
from .config import Settings
from .db import Database
from .models import (
    CreateLinkRequest,
    DailyClicks,
    ErrorResponse,
    LinkResponse,
    ReferrerCount,
    StatsResponse,
)
from .ratelimit import RateLimiter
from .repository import Link, LinkRepository
from .service import (
    AliasTaken,
    IdempotencyConflict,
    InvalidExpiry,
    LinkGone,
    LinkNotFound,
    LinkService,
    owner_id,
    utcnow,
)
from .validation import InvalidURL
from .web import HOME_HTML

log = logging.getLogger("shortener")


class Unauthorized(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after


_ERRORS: dict[type[Exception], tuple[int, str]] = {
    InvalidURL: (400, "invalid_url"),
    InvalidAlias: (400, "invalid_alias"),
    InvalidExpiry: (400, "invalid_expiry"),
    Unauthorized: (401, "unauthorized"),
    LinkNotFound: (404, "not_found"),
    AliasTaken: (409, "alias_taken"),
    LinkGone: (410, "gone"),
    IdempotencyConflict: (422, "idempotency_conflict"),
    RateLimited: (429, "rate_limited"),
}

ERROR_RESPONSES = {status: {"model": ErrorResponse} for status in {s for s, _ in _ERRORS.values()}}


def create_app(
    settings: Settings | None = None,
    clock: Callable[[], datetime] = utcnow,
    limiter_clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.database_path)
    db.migrate()
    service = LinkService(LinkRepository(db), settings, clock)
    limiter = RateLimiter(settings.rate_limit_per_minute, clock=limiter_clock)

    app = FastAPI(title="URL Shortener", version=__version__)
    app.state.service = service

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        log.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
        )
        return response

    for exc_type, (status, code) in _ERRORS.items():

        def handler(request: Request, exc: Exception, status=status, code=code) -> JSONResponse:
            body = ErrorResponse(
                error=code, detail=str(exc) or code, request_id=request.state.request_id
            )
            headers = {}
            if isinstance(exc, RateLimited):
                headers["Retry-After"] = str(max(1, round(exc.retry_after)))
            return JSONResponse(body.model_dump(), status_code=status, headers=headers)

        app.add_exception_handler(exc_type, handler)

    def authenticate(x_api_key: Annotated[str | None, Header()] = None) -> str:
        if not x_api_key or not any(
            hmac.compare_digest(x_api_key.encode(), key.encode()) for key in settings.api_keys
        ):
            raise Unauthorized("missing or invalid X-API-Key")
        return owner_id(x_api_key)

    Owner = Annotated[str, Depends(authenticate)]

    def to_response(link: Link) -> LinkResponse:
        return LinkResponse(
            code=link.code,
            short_url=f"{settings.base_url}/{link.code}",
            target_url=link.target_url,
            created_at=link.created_at,
            expires_at=link.expires_at,
            click_count=link.click_count,
        )

    @app.get("/", include_in_schema=False)
    def home() -> HTMLResponse:
        return HTMLResponse(HOME_HTML)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    def readyz() -> JSONResponse:
        ok = db.ping()
        return JSONResponse({"status": "ready" if ok else "unavailable"}, 200 if ok else 503)

    @app.post(
        "/api/v1/links",
        status_code=201,
        response_model=LinkResponse,
        responses=ERROR_RESPONSES,
        tags=["links"],
    )
    def create_link(
        body: CreateLinkRequest,
        owner: Owner,
        response: Response,
        idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
    ) -> LinkResponse:
        wait = limiter.acquire(owner)
        if wait:
            raise RateLimited(wait)
        link, created = service.create(
            body.url, owner, body.alias, body.expires_at, idempotency_key
        )
        if not created:
            response.status_code = 200
        return to_response(link)

    @app.get(
        "/api/v1/links/{code}",
        response_model=LinkResponse,
        responses=ERROR_RESPONSES,
        tags=["links"],
    )
    def get_link(code: str, owner: Owner) -> LinkResponse:
        return to_response(service.get_owned(code, owner))

    @app.delete("/api/v1/links/{code}", status_code=204, responses=ERROR_RESPONSES, tags=["links"])
    def delete_link(code: str, owner: Owner) -> Response:
        service.delete(code, owner)
        return Response(status_code=204)

    @app.get(
        "/api/v1/links/{code}/stats",
        response_model=StatsResponse,
        responses=ERROR_RESPONSES,
        tags=["analytics"],
    )
    def link_stats(code: str, owner: Owner, days: int = 30) -> StatsResponse:
        stats = service.stats(code, owner, days=max(1, min(days, 365)))
        return StatsResponse(
            code=stats.code,
            total_clicks=stats.total_clicks,
            daily=[DailyClicks(date=d, clicks=n) for d, n in stats.daily],
            top_referrers=[ReferrerCount(host=h, clicks=n) for h, n in stats.top_referrers],
        )

    # Registered last so it never shadows the routes above.
    @app.get("/{code}", status_code=302, responses=ERROR_RESPONSES, tags=["redirect"])
    def follow(
        code: str,
        background: BackgroundTasks,
        referer: Annotated[str | None, Header()] = None,
        user_agent: Annotated[str | None, Header()] = None,
    ) -> RedirectResponse:
        link = service.resolve(code)
        # Recorded after the response is sent, so analytics never slows the redirect.
        background.add_task(service.record_click, link.code, referer, user_agent)
        # 302 + no-store (not 301) so every click reaches us and is counted.
        return RedirectResponse(
            link.target_url, status_code=302, headers={"Cache-Control": "private, no-store"}
        )

    return app
