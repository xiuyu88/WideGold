from __future__ import annotations

from typing import Protocol

from widegold.schemas.indicators import IndicatorFetchRequest, IndicatorSeries


class IndicatorProvider(Protocol):
    provider_name: str

    def fetch(self, request: IndicatorFetchRequest, spec: dict) -> IndicatorSeries:
        ...
