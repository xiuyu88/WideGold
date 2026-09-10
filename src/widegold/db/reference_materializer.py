from __future__ import annotations

from datetime import datetime
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from widegold.db.models import (
    AssetFactorWeightModel,
    AssetModel,
    FactorDefinitionModel,
    IndicatorDefinitionModel,
)


def _version(content: dict, default: str = "1.0.0") -> str:
    return str(content.get("version", default))


def _runtime_meta(factor_id: str, runtime_cfg: dict, resilience_cfg: dict) -> dict:
    value = dict(runtime_cfg.get("defaults", {}))
    value.update(runtime_cfg.get("overrides", {}).get(factor_id, {}))
    if value.get("max_stale_hours") is None:
        freq = value.get("update_frequency", "daily")
        value["max_stale_hours"] = resilience_cfg.get("factor_resolution", {}).get(
            "default_max_stale_hours", {}
        ).get(freq, 72)
    return value


def materialize_reference_tables(
    session: Session,
    configs: Mapping[str, dict],
    *,
    now: datetime,
) -> None:
    """Synchronize convenience reference tables from an ACTIVE config bundle.

    Runtime config JSON in ``reference.config_versions`` remains the canonical production source.
    These tables are query/index friendly materializations for admin screens and foreign keys.
    Removed entities are disabled rather than deleted so historical references remain valid.
    """
    assets_cfg = configs["ASSET_REGISTRY"]
    factors_cfg = configs["FACTOR_SCHEMA"]
    runtime_cfg = configs["DATA_DEFINITION"]
    resilience_cfg = configs["RESILIENCE"]
    indicators_cfg = configs["INDICATOR_REGISTRY"]

    active_asset_ids = {item["id"] for item in assets_cfg.get("assets", [])}
    for row in session.execute(select(AssetModel)).scalars():
        if row.asset_id not in active_asset_ids:
            row.active = False
            row.updated_at = now

    for item in assets_cfg.get("assets", []):
        row = session.get(AssetModel, item["id"])
        if row is None:
            row = AssetModel(asset_id=item["id"], created_at=now, updated_at=now)
            session.add(row)
        row.name = item["name"]
        row.asset_class = item["family"]
        row.currency = item["currency"]
        row.index_code = item.get("index_code")
        row.exchange = item.get("exchange")
        row.active = bool(item.get("active", True))
        row.display_order = int(item.get("display_order", 0))
        row.metadata_json = {
            key: value
            for key, value in item.items()
            if key
            not in {
                "id",
                "name",
                "family",
                "currency",
                "index_code",
                "exchange",
                "active",
                "display_order",
            }
        }
        row.updated_at = now

    active_factor_ids = {item["id"] for item in factors_cfg.get("factors", [])}
    for row in session.execute(select(FactorDefinitionModel)).scalars():
        if row.factor_id not in active_factor_ids:
            row.production_enabled = False
            row.lifecycle = "DEPRECATED"
            row.updated_at = now

    factor_logic_version = _version(runtime_cfg)
    for item in factors_cfg.get("factors", []):
        runtime = _runtime_meta(item["id"], runtime_cfg, resilience_cfg)
        row = session.get(FactorDefinitionModel, item["id"])
        if row is None:
            row = FactorDefinitionModel(
                factor_id=item["id"], created_at=now, updated_at=now
            )
            session.add(row)
        row.name = item["name"]
        row.family = item["family"]
        row.category = item.get("group")
        row.semantic_positive = item.get("semantic_positive")
        row.semantic_negative = item.get("semantic_negative")
        row.evidence_grade = item.get("evidence", "C")
        row.lifecycle = item.get("lifecycle", "APPROVED")
        row.correlation_group = item.get("group")
        row.update_frequency = runtime.get("update_frequency")
        row.max_age_seconds = int(float(runtime["max_stale_hours"]) * 3600)
        row.logic_version = factor_logic_version
        row.production_enabled = bool(item.get("production_enabled", True))
        row.updated_at = now

    active_indicator_ids = {item["id"] for item in indicators_cfg.get("indicators", [])}
    for row in session.execute(select(IndicatorDefinitionModel)).scalars():
        if row.indicator_id not in active_indicator_ids:
            row.active = False
            row.updated_at = now

    indicator_version = _version(indicators_cfg)
    for item in indicators_cfg.get("indicators", []):
        row = session.get(IndicatorDefinitionModel, item["id"])
        if row is None:
            row = IndicatorDefinitionModel(
                indicator_id=item["id"], created_at=now, updated_at=now
            )
            session.add(row)
        row.name = item.get("name", item["id"])
        row.provider = item.get("provider", "external")
        row.source_id = item.get("source_id", item.get("provider", "external"))
        row.frequency = item.get("frequency", "daily")
        row.definition_version = str(item.get("definition_version", indicator_version))
        row.metadata_json = {
            key: value
            for key, value in item.items()
            if key
            not in {
                "id",
                "name",
                "provider",
                "source_id",
                "frequency",
                "definition_version",
                "active",
            }
        }
        row.active = bool(item.get("active", True))
        row.updated_at = now


def ensure_weight_materialization(
    session: Session,
    configs: Mapping[str, dict],
    *,
    now: datetime,
    approved_by=None,
) -> None:
    """Materialize one immutable set of weight rows for the ACTIVE weights version.

    Historical weight config JSON remains authoritative. Existing rows for an already materialized
    version are never rewritten; rollback simply selects that version again at runtime.
    """
    weights_cfg = configs["WEIGHTS"]
    factors_cfg = configs["FACTOR_SCHEMA"]
    weight_version = _version(weights_cfg)
    existing = session.execute(
        select(AssetFactorWeightModel.weight_id)
        .where(AssetFactorWeightModel.weight_version == weight_version)
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return

    factors = {item["id"]: item for item in factors_cfg.get("factors", [])}
    for section in ("equity_sensitivity", "gold_sensitivity"):
        for factor_id, assets in weights_cfg.get(section, {}).items():
            factor = factors.get(factor_id)
            if factor is None:
                continue
            for asset_id, sensitivity in assets.items():
                for horizon, horizon_weight in factor.get("horizons", {}).items():
                    if float(horizon_weight) <= 0:
                        continue
                    session.add(
                        AssetFactorWeightModel(
                            asset_id=asset_id,
                            factor_id=factor_id,
                            horizon=horizon,
                            sensitivity=float(sensitivity),
                            max_contribution=None,
                            weight_version=weight_version,
                            effective_from=now,
                            rationale=(
                                "Materialized from versioned runtime WEIGHTS config; "
                                "config_versions is canonical."
                            ),
                            approved_by=approved_by,
                            approved_at=now if approved_by else None,
                            created_at=now,
                        )
                    )
