from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import ValidationError

from widegold.domain.enums import DataStatus
from widegold.schemas.indicators import (
    ExternalBridgeResponse,
    IndicatorFetchRequest,
    IndicatorObservation,
    IndicatorSeries,
)

EXTERNAL_BRIDGE_CONTRACT_VERSION = "widegold.external-indicator.v1"


class ExternalIndicatorBridgeProvider:
    """Normalized HTTP bridge for MCP/custom collectors.

    WideGold deliberately keeps MCP/vendor transport outside the scoring process. The sidecar
    exposes a versioned HTTP contract, while this provider validates that contract before any row
    is allowed to reach point-in-time storage.

    POST <base_url>/fetch
    {
      "contract_version": "widegold.external-indicator.v1",
      "capability": "china_money.dr007",
      "indicator_id": "CN_DR007",
      "asset_id": null,
      "start_date": "2026-08-01",
      "end_date": "2026-09-10",
      "as_of": "2026-09-10T18:10:00+08:00",
      "force_refresh": false,
      "source_hint": "CHINAMONEY",
      "params": {...}
    }

    The response must repeat contract_version + capability, identify source_id, and provide
    release_ts for every observation. Invalid/mismatched responses degrade to UNAVAILABLE instead
    of poisoning storage or failing the whole analysis run.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._client = client

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _unavailable(indicator_id: str, source_id: str, warning: str) -> IndicatorSeries:
        return IndicatorSeries(
            indicator_id=indicator_id,
            source_id=source_id,
            observations=[],
            status=DataStatus.UNAVAILABLE,
            warnings=[warning],
        )

    def fetch(self, request: IndicatorFetchRequest, spec: dict[str, Any]) -> IndicatorSeries:
        params = spec.get("external_params") or {}
        expected_capability = str(params.get("capability") or "")
        payload = {
            "contract_version": EXTERNAL_BRIDGE_CONTRACT_VERSION,
            "capability": expected_capability,
            "indicator_id": request.indicator_id,
            "asset_id": request.asset_id,
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "as_of": request.as_of.isoformat(),
            "force_refresh": request.force_refresh,
            "source_hint": spec.get("source_id"),
            "params": params,
        }
        client = self._client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self._client is None
        try:
            response = client.post(f"{self.base_url}/fetch", json=payload, headers=self._headers())
            response.raise_for_status()
            raw = response.json()
        finally:
            if close_client:
                client.close()

        fallback_source = str(spec.get("source_id") or "EXTERNAL_BRIDGE")
        try:
            bridge = ExternalBridgeResponse.model_validate(raw)
        except (ValidationError, TypeError, ValueError) as exc:
            return self._unavailable(
                request.indicator_id,
                fallback_source,
                f"external_bridge_contract_invalid:{exc.__class__.__name__}",
            )

        if bridge.contract_version != EXTERNAL_BRIDGE_CONTRACT_VERSION:
            return self._unavailable(
                request.indicator_id,
                bridge.source_id,
                f"external_bridge_contract_version_mismatch:{bridge.contract_version}",
            )
        if bridge.capability != expected_capability:
            return self._unavailable(
                request.indicator_id,
                bridge.source_id,
                f"external_bridge_capability_mismatch:{bridge.capability}",
            )

        warnings = list(bridge.warnings)
        observations: list[IndicatorObservation] = []
        for item in bridge.observations:
            # PIT hard-stop: an External Bridge may never leak an observation whose source
            # release timestamp is later than the AnalysisRun as_of boundary.
            release_ts = item.release_ts
            if release_ts.tzinfo is None:
                release_ts = release_ts.replace(tzinfo=timezone.utc)
            if release_ts > request.as_of:
                warnings.append("external_bridge_release_after_as_of_dropped")
                continue
            ingest_ts = item.ingest_ts or datetime.now(timezone.utc)
            observations.append(
                IndicatorObservation(
                    indicator_id=request.indicator_id,
                    asset_id=item.asset_id if item.asset_id is not None else request.asset_id,
                    observation_date=item.observation_date,
                    release_ts=release_ts,
                    ingest_ts=ingest_ts,
                    value=float(item.value),
                    source_id=str(item.source_id or bridge.source_id),
                    revision_vintage=item.revision_vintage,
                    definition_version=str(
                        item.definition_version
                        or spec.get("definition_version")
                        or "external-1.0.0"
                    ),
                    status=item.status,
                    metadata=dict(item.metadata),
                )
            )

        series_status = bridge.status
        if not observations and series_status == DataStatus.VALID:
            series_status = DataStatus.UNAVAILABLE
            warnings.append("external_bridge_empty")

        return IndicatorSeries(
            indicator_id=request.indicator_id,
            source_id=bridge.source_id,
            observations=observations,
            status=series_status,
            warnings=warnings,
        )
