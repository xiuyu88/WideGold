from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from time import perf_counter

from widegold.data.providers.news_akshare import AkshareGlobalNewsProvider
from widegold.domain.enums import DataStatus, SourceTier
from widegold.repositories.factory import repository
from widegold.resilience.executor import ResilientExecutor
from widegold.settings.app import get_settings
from widegold.settings.config import news_config


@dataclass
class NewsCollectionResult:
    documents: list = field(default_factory=list)
    status: DataStatus = DataStatus.UNAVAILABLE
    warnings: list[str] = field(default_factory=list)


class NewsCollectionService:
    """Collect a bounded, relevant end-of-day news set without making news a hard dependency."""

    def __init__(self, *, repo=None, executor: ResilientExecutor | None = None) -> None:
        self.repo = repo or repository()
        self.executor = executor or ResilientExecutor()

    def collect(self, as_of: datetime):
        settings = get_settings()
        mode = settings.news_mode.lower()
        if mode == "disabled":
            return NewsCollectionResult(
                documents=[], status=DataStatus.UNAVAILABLE, warnings=["news_collection_disabled"]
            )
        if mode != "akshare":
            raise RuntimeError(f"Unsupported WIDEGOLD_NEWS_MODE: {settings.news_mode}")

        cfg = news_config()
        provider_cfg = cfg["providers"]["akshare_global_em"]
        provider = AkshareGlobalNewsProvider()
        started = perf_counter()
        result = self.executor.call_sync(
            capability="news:akshare_global_em",
            primary=(
                provider.provider_name,
                lambda: provider.fetch(
                    as_of=as_of,
                    lookback_hours=int(cfg.get("lookback_hours", 30)),
                    max_documents=int(cfg.get("max_documents", 40)),
                    keywords=list(cfg.get("relevance_keywords", [])),
                    minimum_relevance_score=int(cfg.get("minimum_relevance_score", 1)),
                    source_id=provider_cfg.get("source_id", "EASTMONEY_NEWS"),
                    source_tier=SourceTier(provider_cfg.get("source_tier", "C")),
                ),
            ),
        )
        latency_ms = int((perf_counter() - started) * 1000)
        if result.ok:
            documents = list(result.data or [])
            self.repo.update_provider_health(
                "news:akshare_global_em",
                status=DataStatus.VALID.value,
                latency_ms=latency_ms,
                details={"documents": len(documents), "lookback_hours": cfg.get("lookback_hours", 30)},
            )
            return NewsCollectionResult(documents=documents, status=DataStatus.VALID, warnings=[])

        errors = [error.message for error in result.errors]
        self.repo.update_provider_health(
            "news:akshare_global_em",
            status=DataStatus.UNAVAILABLE.value,
            latency_ms=latency_ms,
            details={"errors": errors},
        )
        # News failure degrades event-only intelligence but must not crash the numeric pipeline.
        return NewsCollectionResult(documents=[], status=DataStatus.UNAVAILABLE, warnings=errors)
