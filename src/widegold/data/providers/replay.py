from __future__ import annotations

from datetime import datetime, timedelta

from widegold.domain.enums import DataStatus
from widegold.engine.factor_calculators import calculate_all_factor_inputs, required_indicator_keys
from widegold.repositories.factory import repository
from widegold.schemas.providers import MockMarketBundle
from widegold.settings.config import news_config


class StoredPointInTimeProvider:
    """Replay provider that never reaches the network.

    Only observations/documents already present in WideGold and visible at ``as_of`` are used.
    Missing historical news is treated as unavailable rather than silently backfilled from the
    current internet, preserving the point-in-time contract.
    """

    provider_name = "stored_point_in_time"

    def __init__(self, *, as_of: datetime, repo=None) -> None:
        self.as_of = as_of
        self.repo = repo or repository()
        self._factor_inputs = None
        self._news = None

    def factor_inputs(self):
        if self._factor_inputs is None:
            histories = {
                key: self.repo.get_indicator_history(
                    key[0], as_of=self.as_of, asset_id=key[1], limit=1200
                )
                for key in required_indicator_keys()
            }
            self._factor_inputs = calculate_all_factor_inputs(histories, self.as_of)
        return list(self._factor_inputs)

    def load(self) -> MockMarketBundle:
        if self._news is None:
            cfg = news_config()
            start = self.as_of - timedelta(hours=int(cfg.get("lookback_hours", 30)))
            self._news = self.repo.get_documents_for_window(
                start=start,
                end=self.as_of,
                retrieved_before=self.as_of,
                limit=int(cfg.get("max_documents", 40)),
            )
        factor_inputs = self.factor_inputs()
        # Conservative replay semantics: a non-empty stored discovery set proves that news was
        # available. An empty set cannot distinguish "no relevant event" from "not collected".
        news_status = DataStatus.VALID if self._news else DataStatus.UNAVAILABLE
        warnings = [] if self._news else ["replay_news_history_unavailable"]
        return MockMarketBundle(
            analysis_date=self.as_of.date().isoformat(),
            as_of=self.as_of,
            base_factor_states={
                row.factor_id: row.value
                for row in factor_inputs
                if row.asset_id is None and row.value is not None
            },
            news=list(self._news),
            news_status=news_status,
            news_warnings=warnings,
        )
