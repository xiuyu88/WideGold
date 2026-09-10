from __future__ import annotations

from widegold.domain.enums import DataStatus
from widegold.schemas.factors import FactorState
from widegold.schemas.resilience import AssetCoverage, QualityGateResult
from widegold.settings.config import asset_config, factor_config, resilience_config, weight_config


def _sensitivity(asset_id: str, factor_id: str) -> float:
    cfg = weight_config()
    for section in ("equity_sensitivity", "gold_sensitivity"):
        value = cfg.get(section, {}).get(factor_id, {}).get(asset_id)
        if value is not None:
            return abs(float(value))
    return 0.0


def _states_for_asset(asset_id: str, states: list[FactorState]) -> dict[str, FactorState]:
    """Select global states plus asset-specific overrides for one asset.

    Asset-scoped factor rows coexist in the physical FactorState list. Coverage must use the
    same scoping rule as scoring; otherwise the last physical row for EQ06/EQ07/EQ14 can leak
    into every equity asset's Quality Gate decision.
    """
    chosen: dict[str, FactorState] = {}
    for state in states:
        if state.asset_id not in {None, asset_id}:
            continue
        current = chosen.get(state.factor_id)
        if current is None or (state.asset_id == asset_id and current.asset_id is None):
            chosen[state.factor_id] = state
    return chosen


def calculate_asset_coverage(asset_id: str, states: list[FactorState]) -> AssetCoverage:
    state_map = _states_for_asset(asset_id, states)
    factor_defs = {item["id"]: item for item in factor_config()["factors"]}
    asset_family = next(a["family"] for a in asset_config()["assets"] if a["id"] == asset_id)
    critical = set(resilience_config()["critical_factors"].get(asset_family, []))

    total_weight = 0.0
    covered_weight = 0.0
    fresh_covered_weight = 0.0
    lkg_covered_weight = 0.0
    relevant_count = 0
    unavailable_count = 0
    critical_weight = 0.0
    critical_covered = 0.0
    missing_critical: list[str] = []

    for factor_id in factor_defs:
        sensitivity = _sensitivity(asset_id, factor_id)
        if sensitivity <= 0:
            continue
        relevant_count += 1
        total_weight += sensitivity
        state = state_map.get(factor_id)
        coverage = state.coverage if state else 0.0
        if state is None or state.status == DataStatus.UNAVAILABLE:
            unavailable_count += 1
        covered_weight += sensitivity * coverage
        if state is not None and "last_known_good" not in state.quality_flags:
            fresh_covered_weight += sensitivity * coverage
        elif state is not None and "last_known_good" in state.quality_flags:
            lkg_covered_weight += sensitivity * coverage
        if factor_id in critical:
            critical_weight += sensitivity
            critical_covered += sensitivity * coverage
            if coverage <= 0:
                missing_critical.append(factor_id)

    weighted = covered_weight / total_weight if total_weight else 0.0
    fresh_weighted = fresh_covered_weight / total_weight if total_weight else 0.0
    lkg_ratio = lkg_covered_weight / total_weight if total_weight else 0.0
    critical_cov = critical_covered / critical_weight if critical_weight else 1.0
    unavailable_ratio = unavailable_count / relevant_count if relevant_count else 1.0
    q = resilience_config()["quality_gate"]
    scoreable = (
        weighted >= float(q["minimum_preview_weighted_coverage"])
        and unavailable_ratio <= float(q["maximum_unavailable_factor_ratio"])
    )
    return AssetCoverage(
        asset_id=asset_id,
        weighted_coverage=weighted,
        fresh_weighted_coverage=fresh_weighted,
        last_known_good_weighted_ratio=lkg_ratio,
        critical_coverage=critical_cov,
        unavailable_factor_ratio=unavailable_ratio,
        scoreable=scoreable,
        missing_critical=missing_critical,
    )


def run_quality_gate(states: list[FactorState]) -> QualityGateResult:
    q = resilience_config()["quality_gate"]
    assets = [calculate_asset_coverage(a["id"], states) for a in asset_config()["assets"]]
    overall = sum(a.weighted_coverage for a in assets) / len(assets) if assets else 0.0

    preview = all(a.scoreable for a in assets) if q.get("require_all_assets_scoreable", True) else any(a.scoreable for a in assets)
    min_fresh_publish = float(q.get("minimum_publish_fresh_weighted_coverage", 0.0))
    publish = preview and all(
        a.weighted_coverage >= float(q["minimum_publish_weighted_coverage"])
        and a.fresh_weighted_coverage >= min_fresh_publish
        and a.critical_coverage >= float(q["minimum_critical_coverage"])
        for a in assets
    )

    failures: list[str] = []
    warnings: list[str] = []
    for asset in assets:
        if not asset.scoreable:
            failures.append(f"{asset.asset_id}: insufficient weighted coverage {asset.weighted_coverage:.1%}")
        elif asset.weighted_coverage < float(q["minimum_publish_weighted_coverage"]):
            warnings.append(f"{asset.asset_id}: preview only coverage {asset.weighted_coverage:.1%}")
        if asset.fresh_weighted_coverage < min_fresh_publish:
            warnings.append(
                f"{asset.asset_id}: fresh coverage {asset.fresh_weighted_coverage:.1%} below publish floor "
                f"{min_fresh_publish:.1%}; last-known-good cannot create a new published snapshot"
            )
        if asset.missing_critical:
            warnings.append(f"{asset.asset_id}: missing critical factors {','.join(asset.missing_critical)}")

    return QualityGateResult(
        passed_for_preview=preview,
        passed_for_publish=publish,
        assets=assets,
        overall_weighted_coverage=overall,
        failures=failures,
        warnings=warnings,
    )
