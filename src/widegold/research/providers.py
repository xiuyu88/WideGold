from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from widegold.research.search import SearchRequest, SearchResponse, SearchResult
from widegold.settings.app import get_settings


class TavilySearchProvider:
    provider_name = "tavily"

    def __init__(self, api_key: str, base_url: str = "https://api.tavily.com") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def search(self, request: SearchRequest) -> SearchResponse:
        payload = {
            "query": request.query,
            "search_depth": "advanced",
            "max_results": request.max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        if request.domains:
            payload["include_domains"] = request.domains
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/search", headers=headers, json=payload)
            response.raise_for_status()
        body = response.json()
        results = []
        for rank, item in enumerate(body.get("results", []), start=1):
            url = item.get("url", "")
            results.append(SearchResult(
                title=item.get("title") or url,
                url=url,
                snippet=item.get("content"),
                published_at=None,
                source_domain=urlparse(url).netloc,
                provider_rank=rank,
                metadata={"score": item.get("score")},
            ))
        return SearchResponse(provider=self.provider_name, query=request.query, results=results)


class BraveSearchProvider:
    provider_name = "brave"

    def __init__(self, api_key: str, base_url: str = "https://api.search.brave.com") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def search(self, request: SearchRequest) -> SearchResponse:
        query = request.query
        if request.domains:
            query += " " + " OR ".join(f"site:{d}" for d in request.domains)
        headers = {"X-Subscription-Token": self.api_key, "Accept": "application/json"}
        params = {"q": query, "count": min(request.max_results, 20)}
        if request.language:
            params["search_lang"] = request.language.split("-")[0]
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.base_url}/res/v1/web/search", headers=headers, params=params
            )
            response.raise_for_status()
        body = response.json()
        results = []
        for rank, item in enumerate(body.get("web", {}).get("results", []), start=1):
            url = item.get("url", "")
            results.append(SearchResult(
                title=item.get("title") or url,
                url=url,
                snippet=item.get("description"),
                published_at=None,
                source_domain=urlparse(url).netloc,
                provider_rank=rank,
                metadata={"profile": item.get("profile")},
            ))
        return SearchResponse(provider=self.provider_name, query=request.query, results=results)


class MockSearchProvider:
    provider_name = "mock"

    async def search(self, request: SearchRequest) -> SearchResponse:
        now = datetime.now(timezone.utc)
        results = [
            SearchResult(
                title=f"Mock official evidence for {request.query}",
                url="https://example.com/official-evidence",
                snippet="Official-style evidence placeholder for deterministic development tests.",
                published_at=now,
                source_domain="example.com",
                provider_rank=1,
                metadata={"mock": True},
            ),
            SearchResult(
                title=f"Mock contradictory evidence for {request.query}",
                url="https://example.org/contradictory-evidence",
                snippet="Contradictory placeholder evidence used to verify governance paths.",
                published_at=now,
                source_domain="example.org",
                provider_rank=2,
                metadata={"mock": True},
            ),
        ]
        return SearchResponse(provider=self.provider_name, query=request.query, results=results)


def search_provider():
    settings = get_settings()
    if settings.research_mode == "mock":
        return MockSearchProvider()
    if settings.research_mode == "disabled":
        raise RuntimeError("Factor Research is disabled; configure SEARCH_PROVIDER and WIDEGOLD_RESEARCH_MODE=live")
    provider = (settings.search_provider or "").lower()
    if not settings.search_api_key:
        raise RuntimeError("SEARCH_API_KEY is required for live Factor Research")
    if provider == "tavily":
        return TavilySearchProvider(settings.search_api_key, settings.search_base_url or "https://api.tavily.com")
    if provider == "brave":
        return BraveSearchProvider(settings.search_api_key, settings.search_base_url or "https://api.search.brave.com")
    raise RuntimeError(f"Unsupported SEARCH_PROVIDER: {settings.search_provider}")
