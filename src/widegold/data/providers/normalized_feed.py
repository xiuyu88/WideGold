from __future__ import annotations

from datetime import datetime

import httpx

from widegold.domain.enums import DataStatus, SourceTier
from widegold.schemas.events import NewsDocument
from widegold.schemas.providers import MockMarketBundle
from widegold.schemas.resilience import FactorInput


class NormalizedFeedProvider:
    """Integration contract for a real data/MCP aggregation layer.

    Expected JSON:
    {
      "analysis_date": "YYYY-MM-DD",
      "as_of": "ISO8601",
      "factors": [{"factor_id":..., "value":..., "observed_at":..., "status":"VALID", ...}],
      "news": [{"source_id":..., "source_tier":"S", "title":..., "content":..., "published_at":...}]
    }

    This lets the factor engine remain stable while individual official/API/MCP collectors evolve.
    """

    provider_name = "normalized_feed"

    def __init__(self, url: str, timeout_seconds: float = 30.0) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self._payload: dict | None = None

    def _fetch(self) -> dict:
        if self._payload is None:
            response = httpx.get(self.url, timeout=self.timeout_seconds)
            response.raise_for_status()
            self._payload = response.json()
        return self._payload

    def load(self) -> MockMarketBundle:
        payload = self._fetch()
        as_of = datetime.fromisoformat(payload["as_of"])
        docs = [
            NewsDocument(
                source_id=item["source_id"],
                source_tier=SourceTier(item.get("source_tier", "C")),
                title=item["title"],
                content=item.get("content", item["title"]),
                published_at=datetime.fromisoformat(item["published_at"]),
                retrieved_at=as_of,
                url=item.get("url"),
                language=item.get("language", "zh-CN"),
            )
            for item in payload.get("news", [])
        ]
        return MockMarketBundle(
            analysis_date=payload["analysis_date"],
            as_of=as_of,
            base_factor_states={
                item["factor_id"]: item["value"]
                for item in payload.get("factors", [])
                if item.get("value") is not None
            },
            news=docs,
        )

    def factor_inputs(self) -> list[FactorInput]:
        payload = self._fetch()
        as_of = datetime.fromisoformat(payload["as_of"])
        rows = []
        for item in payload.get("factors", []):
            observed = item.get("observed_at")
            rows.append(FactorInput(
                factor_id=item["factor_id"],
                value=item.get("value"),
                observed_at=datetime.fromisoformat(observed) if observed else as_of,
                status=DataStatus(item.get("status", "VALID")),
                reliability=float(item.get("reliability", 0.8)),
                source_ids=item.get("source_ids", [self.provider_name]),
                warnings=item.get("warnings", []),
            ))
        return rows
