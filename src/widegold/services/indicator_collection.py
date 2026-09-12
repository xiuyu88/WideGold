from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from time import perf_counter
from typing import Callable

from widegold.data.providers.indicator_registry import indicator_provider
from widegold.domain.enums import DataStatus
from widegold.repositories.factory import repository
from widegold.resilience.executor import ResilientExecutor
from widegold.schemas.indicators import IndicatorFetchRequest, IndicatorObservation, IndicatorSeries
from widegold.settings.app import get_settings
from widegold.services.indicator_ingest import validate_indicator_observations
from widegold.settings.config import indicator_config, resilience_config

IndicatorKey = tuple[str, str | None]


@dataclass
class IndicatorCollectionResult:
    histories: dict[IndicatorKey, list[IndicatorObservation]] = field(default_factory=dict)
    fetched: int = 0
    external_only: int = 0
    unavailable: int = 0
    warnings: list[str] = field(default_factory=list)


def _provider_health_key(provider_name: str, indicator_id: str, asset_id: str | None) -> str:
    scope = f":{asset_id}" if asset_id else ""
    return f"{provider_name}:{indicator_id}{scope}"


class IndicatorCollectionService:
    """Collect independent raw indicators with failure isolation and bounded parallelism.

    The service is intentionally below Prefect. It can run in unit tests/direct mode and can
    later be called from Prefect mapped tasks without changing Provider or Calculator contracts.
    External/MCP indicators are read from the point-in-time store and never guessed here.
    """

    def __init__(
        self,
        *,
        repo=None,
        executor: ResilientExecutor | None = None,
        provider_factory: Callable[[str], object] = indicator_provider,
        specs: dict[str, dict] | None = None,
        max_workers: int | None = None,
    ) -> None:
        self.repo = repo or repository()
        self.executor = executor or ResilientExecutor()
        self.provider_factory = provider_factory
        self.specs = specs or {row["id"]: row for row in indicator_config()["indicators"]}
        cfg = resilience_config().get("indicator_collection", {})
        self.max_workers = max(1, int(max_workers or cfg.get("max_workers", 4)))

    def _read_history(self, key: IndicatorKey, as_of: datetime, limit: int = 600):
        indicator_id, asset_id = key
        return self.repo.get_indicator_history(indicator_id, as_of=as_of, asset_id=asset_id, limit=limit)

    def _collect_one(
        self, key: IndicatorKey, as_of: datetime, force_refresh: bool = False
    ) -> tuple[IndicatorKey, list[IndicatorObservation], str, list[str]]:
        indicator_id, asset_id = key
        spec = self.specs.get(indicator_id)
        if spec is None:
            return key, [], "missing_spec", [f"indicator_registry_missing:{indicator_id}"]

        existing = self._read_history(key, as_of)
        provider_name = spec.get("provider", "external")
        original_provider_name = provider_name
        if provider_name == "external":
            # External indicators can be populated in two ways:
            # 1) an out-of-process MCP/custom collector writes normalized observations to the DB;
            # 2) an optional HTTP bridge is configured and WideGold fetches them on demand.
            if get_settings().external_indicator_url:
                provider_name = "external_bridge"
            else:
                return key, existing, "external", ([] if existing else [f"external_indicator_missing:{indicator_id}"])

        lookback_days = int(spec.get("lookback_days", 1000))
        request = IndicatorFetchRequest(
            indicator_id=indicator_id,
            asset_id=asset_id,
            start_date=as_of.date() - timedelta(days=lookback_days),
            end_date=as_of.date(),
            as_of=as_of,
            force_refresh=force_refresh,
        )
        provider = self.provider_factory(provider_name)
        started = perf_counter()
        result = self.executor.call_sync(
            capability=f"indicator:{indicator_id}",
            primary=(provider_name, lambda p=provider, r=request, s=spec: p.fetch(r, s)),
        )
        latency_ms = int((perf_counter() - started) * 1000)
        health_key = _provider_health_key(provider_name, indicator_id, asset_id)
        warnings: list[str] = []

        if result.ok and isinstance(result.data, IndicatorSeries):
            series = result.data
            observations = []
            for row in series.observations:
                if row.asset_id is None and asset_id is not None:
                    row = row.model_copy(update={"asset_id": asset_id})
                observations.append(row)
            contract_ok = True
            if observations:
                try:
                    validate_indicator_observations(observations, specs=self.specs)
                except ValueError as exc:
                    contract_ok = False
                    warnings.append(f"indicator_contract_rejected:{indicator_id}:{exc}")
                    observations = []
                else:
                    self.repo.save_indicator_observations(observations)
            effective_status = series.status if observations and contract_ok else DataStatus.UNAVAILABLE
            warnings.extend(series.warnings)
            self.repo.update_provider_health(
                health_key,
                status=effective_status.value,
                latency_ms=latency_ms,
                details={
                    "provider": provider_name,
                    "logical_provider": original_provider_name,
                    "indicator_id": indicator_id,
                    "asset_id": asset_id,
                    "rows": len(observations),
                    "warnings": series.warnings,
                },
            )
        else:
            warnings.extend(error.message for error in result.errors)
            self.repo.update_provider_health(
                health_key,
                status=DataStatus.UNAVAILABLE.value,
                latency_ms=latency_ms,
                details={
                    "provider": provider_name,
                    "logical_provider": original_provider_name,
                    "indicator_id": indicator_id,
                    "asset_id": asset_id,
                    "errors": warnings,
                },
            )

        history = self._read_history(key, as_of) or existing
        if provider_name == "external_bridge" and history and not result.ok:
            status = "external_cached"
            warnings.append(f"external_bridge_fallback_to_stored:{indicator_id}")
        else:
            status = "fetched" if history else "unavailable"
        if not history and not warnings:
            warnings.append(f"indicator_unavailable:{indicator_id}")
        return key, history, status, warnings

    def collect(
        self, keys: set[IndicatorKey], as_of: datetime, *, force_refresh: bool = False
    ) -> IndicatorCollectionResult:
        result = IndicatorCollectionResult()
        ordered = sorted(keys, key=lambda x: (x[0], x[1] or ""))
        if not ordered:
            return result

        workers = min(self.max_workers, len(ordered))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="widegold-indicator") as pool:
            # ThreadPoolExecutor workers start with a fresh context, which would drop the frozen
            # runtime-config snapshot installed by version_snapshot_scope and let a mid-run config
            # activation change what this analysis reads.
            context = contextvars.copy_context()
            futures = {
                pool.submit(context.run, self._collect_one, key, as_of, force_refresh): key
                for key in ordered
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    resolved_key, history, status, warnings = future.result()
                except Exception as exc:  # final isolation boundary; one indicator cannot kill the batch
                    resolved_key, history, status, warnings = key, [], "unavailable", [
                        f"indicator_collection_failed:{key[0]}:{exc}"
                    ]
                result.histories[resolved_key] = history
                result.warnings.extend(warnings)
                if status in {"external", "external_cached"}:
                    result.external_only += 1
                    if status == "external_cached":
                        result.unavailable += 1
                elif status == "fetched":
                    result.fetched += 1
                else:
                    result.unavailable += 1
        return result
