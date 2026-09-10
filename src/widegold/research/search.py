from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol

from pydantic import Field

from widegold.schemas.common import StrictModel


class SearchRequest(StrictModel):
    query: str
    domains: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    language: str | None = None
    max_results: int = Field(default=10, ge=1, le=50)


class SearchResult(StrictModel):
    title: str
    url: str
    snippet: str | None = None
    published_at: datetime | None = None
    source_domain: str
    provider_rank: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(StrictModel):
    provider: str
    query: str
    results: list[SearchResult]


class SearchProvider(Protocol):
    async def search(self, request: SearchRequest) -> SearchResponse:
        ...
