from __future__ import annotations

from datetime import datetime, timezone

from widegold.engine.factor_calculators import required_indicator_keys
from widegold.repositories.factory import repository
from widegold.settings.app import get_settings
from widegold.settings.config import indicator_config, resilience_config


def external_indicator_diagnostics(*, as_of: datetime | None = None) -> list[dict]:
    """Describe the normalized external/MCP inputs WideGold still depends on.

    This is deliberately a read-only operational view. It helps an administrator distinguish
    "bridge not configured", "bridge configured but never populated", and "stored last-known-good"
    without exposing MCP implementation details to the scoring core.
    """
    as_of = as_of or datetime.now(timezone.utc)
    specs = {row["id"]: row for row in indicator_config().get("indicators", [])}
    freshness_cfg = resilience_config().get("indicator_freshness", {})
    hard_by_freq = freshness_cfg.get("hard_max_age_hours", {})
    bridge_configured = bool(get_settings().external_indicator_url)
    repo = repository()
    result: list[dict] = []

    for indicator_id, asset_id in sorted(required_indicator_keys(), key=lambda x: (x[0], x[1] or "")):
        spec = specs.get(indicator_id, {})
        if spec.get("provider") != "external":
            continue
        rows = repo.get_indicator_history(indicator_id, as_of=as_of, asset_id=asset_id, limit=5)
        latest = rows[-1] if rows else None
        frequency = str(spec.get("frequency", "daily"))
        max_age_hours = float(hard_by_freq.get(frequency, 168))
        age_hours = None
        operational_status = "MISSING"
        if latest is not None:
            observation_ts = datetime.combine(latest.observation_date, datetime.max.time(), tzinfo=as_of.tzinfo)
            age_hours = max(0.0, (as_of - observation_ts).total_seconds() / 3600.0)
            operational_status = "STALE" if age_hours > max_age_hours else "AVAILABLE"
        result.append({
            "indicator_id": indicator_id,
            "asset_id": asset_id,
            "name": spec.get("name", indicator_id),
            "source_id": spec.get("source_id"),
            "frequency": frequency,
            "capability": (spec.get("external_params") or {}).get("capability"),
            "unit": (spec.get("external_params") or {}).get("unit"),
            "semantic": (spec.get("external_params") or {}).get("semantic"),
            "expected_range": (spec.get("external_params") or {}).get("expected_range"),
            "asset_scoped": bool((spec.get("external_params") or {}).get("asset_scoped", False)),
            "bridge_configured": bridge_configured,
            "operational_status": operational_status,
            "latest_observation_date": latest.observation_date.isoformat() if latest else None,
            "latest_release_ts": latest.release_ts.isoformat() if latest else None,
            "age_hours": round(age_hours, 1) if age_hours is not None else None,
            "last_source_id": latest.source_id if latest else None,
        })
    return result
