"""HTTP request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., max_length=2048, examples=["https://example.com/a/long/path"])
    alias: str | None = Field(None, min_length=3, max_length=32, examples=["spring-sale"])
    expires_at: datetime | None = Field(None, description="ISO-8601; must be in the future")


class LinkResponse(BaseModel):
    code: str
    short_url: str
    target_url: str
    created_at: datetime
    expires_at: datetime | None
    click_count: int


class DailyClicks(BaseModel):
    date: str
    clicks: int


class ReferrerCount(BaseModel):
    host: str
    clicks: int


class StatsResponse(BaseModel):
    code: str
    total_clicks: int
    daily: list[DailyClicks]
    top_referrers: list[ReferrerCount]


class ErrorResponse(BaseModel):
    error: str
    detail: str
    request_id: str | None = None
