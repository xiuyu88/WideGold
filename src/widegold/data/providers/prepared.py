from __future__ import annotations

from datetime import datetime

from widegold.domain.enums import DataStatus
from widegold.schemas.providers import MockMarketBundle
from widegold.schemas.resilience import FactorInput


class PreparedFactorInputProvider:
    """Adapter used by orchestrators after raw indicator tasks have already completed."""

    provider_name = "prepared_factor_inputs"

    def __init__(
        self,
        *,
        as_of: datetime,
        factor_inputs: list[FactorInput],
        news=None,
        news_status: DataStatus = DataStatus.VALID,
        news_warnings: list[str] | None = None,
    ) -> None:
        self.as_of = as_of
        self._factor_inputs = list(factor_inputs)
        self._news = list(news or [])
        self._news_status = news_status
        self._news_warnings = list(news_warnings or [])

    def load(self) -> MockMarketBundle:
        return MockMarketBundle(
            analysis_date=self.as_of.date().isoformat(),
            as_of=self.as_of,
            base_factor_states={
                row.factor_id: row.value
                for row in self._factor_inputs
                if row.asset_id is None and row.value is not None
            },
            news=self._news,
            news_status=self._news_status,
            news_warnings=self._news_warnings,
        )

    def factor_inputs(self) -> list[FactorInput]:
        return list(self._factor_inputs)
