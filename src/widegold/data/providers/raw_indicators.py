from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from widegold.domain.enums import DataStatus
from widegold.engine.factor_calculators import calculate_all_factor_inputs, required_indicator_keys
from widegold.schemas.providers import MockMarketBundle
from widegold.services.indicator_collection import IndicatorCollectionService
from widegold.services.news_collection import NewsCollectionService


class RawIndicatorAnalysisProvider:
    """Raw indicator store/fetch -> calculator registry -> normalized FactorInput.

    Indicator collection is delegated to a bounded-parallel service. Individual HTTP/MCP-like
    capability failures degrade only their own indicator; factor resolution/quality gates decide
    whether the resulting analysis remains scoreable/publishable.
    """

    provider_name = "raw_indicators"

    def __init__(
        self,
        as_of: datetime | None = None,
        collector: IndicatorCollectionService | None = None,
        *,
        force_refresh: bool = False,
    ) -> None:
        self.as_of = as_of or datetime.now(ZoneInfo("Asia/Shanghai"))
        self.collector = collector or IndicatorCollectionService()
        self.force_refresh = force_refresh
        self._collection = None
        self._factor_inputs = None
        self._news_result = None

    def load(self) -> MockMarketBundle:
        self._ensure_collected()
        return MockMarketBundle(
            analysis_date=self.as_of.date().isoformat(),
            as_of=self.as_of,
            base_factor_states={
                row.factor_id: row.value
                for row in (self._factor_inputs or [])
                if row.asset_id is None and row.value is not None
            },
            news=list(self._news_result.documents if self._news_result else []),
            news_status=self._news_result.status if self._news_result else DataStatus.UNAVAILABLE,
            news_warnings=list(self._news_result.warnings if self._news_result else []),
        )

    def factor_inputs(self):
        self._ensure_collected()
        return list(self._factor_inputs or [])

    @property
    def collection_summary(self) -> dict:
        self._ensure_collected()
        collection = self._collection
        return {
            "fetched": collection.fetched,
            "external_only": collection.external_only,
            "unavailable": collection.unavailable,
            "warnings": list(collection.warnings),
        }

    def _ensure_collected(self) -> None:
        if self._factor_inputs is not None:
            return
        self._collection = self.collector.collect(
            required_indicator_keys(), self.as_of, force_refresh=self.force_refresh
        )
        self._factor_inputs = calculate_all_factor_inputs(self._collection.histories, self.as_of)
        self._news_result = NewsCollectionService(repo=self.collector.repo).collect(self.as_of)
