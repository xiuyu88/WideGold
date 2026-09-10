from __future__ import annotations

from datetime import datetime, timezone

import httpx

from widegold.domain.enums import DataStatus
from widegold.schemas.indicators import IndicatorFetchRequest, IndicatorObservation, IndicatorSeries


class FredIndicatorProvider:
    provider_name = "fred"
    base_url = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, api_key: str | None, timeout_seconds: float = 20.0) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch(self, request: IndicatorFetchRequest, spec: dict) -> IndicatorSeries:
        if not self.api_key:
            return IndicatorSeries(
                indicator_id=request.indicator_id, source_id=spec.get("source_id", "FRED"),
                status=DataStatus.UNAVAILABLE, warnings=["FRED_API_KEY is not configured"]
            )
        series_id = spec["params"]["series_id"]
        as_of_day = request.as_of.date().isoformat()
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": request.start_date.isoformat(),
            "observation_end": request.end_date.isoformat(),
            "realtime_start": as_of_day,
            "realtime_end": as_of_day,
            "sort_order": "asc",
        }
        # FRED can transform a raw series server-side (for example units=pc1 for
        # percent change from year ago). Only explicitly allow safe observation
        # transformation parameters from the registry; point-in-time parameters
        # remain owned by this provider.
        for key in ("units", "frequency", "aggregation_method"):  # pragma: no branch
            value = spec.get("params", {}).get(key)
            if value is not None:
                params[key] = value
        response = httpx.get(self.base_url, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        now = datetime.now(timezone.utc)
        rows: list[IndicatorObservation] = []
        for item in payload.get("observations", []):
            if item.get("value") in {None, "."}:
                continue
            rows.append(IndicatorObservation(
                indicator_id=request.indicator_id,
                asset_id=request.asset_id,
                observation_date=datetime.fromisoformat(item["date"]).date(),
                release_ts=request.as_of,
                ingest_ts=now,
                value=float(item["value"]),
                source_id=spec.get("source_id", "FRED"),
                revision_vintage=item.get("realtime_start", as_of_day),
                definition_version=spec.get("definition_version", "1.0.0"),
                metadata={"fred_series_id": series_id, "realtime_end": item.get("realtime_end")},
            ))
        status = DataStatus.VALID if rows else DataStatus.UNAVAILABLE
        return IndicatorSeries(
            indicator_id=request.indicator_id, source_id=spec.get("source_id", "FRED"),
            observations=rows, status=status,
            warnings=[] if rows else [f"FRED returned no observations for {series_id}"],
        )
