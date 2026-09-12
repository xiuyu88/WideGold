from __future__ import annotations

from collections import defaultdict
from uuid import uuid4

from widegold.domain.enums import DataStatus, Horizon
from widegold.engine.rules import RuleEffects
from widegold.schemas.common import VersionSnapshot
from widegold.schemas.factors import FactorState
from widegold.schemas.scores import AssetScore, FactorContribution
from widegold.settings.config import asset_config, factor_config, weight_config

HORIZON_BLEND = {
    Horizon.TACTICAL.value: 0.45,
    Horizon.SWING.value: 0.35,
    Horizon.STRATEGIC.value: 0.20,
}


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def score_label(score: float) -> str:
    if score >= 80:
        return "强势偏积极"
    if score >= 65:
        return "偏积极"
    if score >= 45:
        return "中性/观察"
    if score >= 30:
        return "偏谨慎"
    return "强风险警示"


def _factor_metadata() -> dict[str, dict]:
    return {item["id"]: item for item in factor_config()["factors"]}


def _asset_metadata() -> dict[str, dict]:
    return {item["id"]: item for item in asset_config()["assets"]}


def sensitivity(asset_id: str, factor_id: str) -> float:
    cfg = weight_config()
    for section in ("equity_sensitivity", "gold_sensitivity"):
        value = cfg.get(section, {}).get(factor_id, {}).get(asset_id)
        if value is not None:
            return float(value)
    return 0.0


def _raw_horizon_contributions(
    asset_id: str,
    states: list[FactorState],
    horizon: str,
    effects: RuleEffects,
) -> list[dict]:
    meta = _factor_metadata()
    rows: list[dict] = []
    chosen: dict[str, FactorState] = {}
    for state in states:
        if state.asset_id not in {None, asset_id}:
            continue
        current = chosen.get(state.factor_id)
        if current is None or (state.asset_id == asset_id and current.asset_id is None):
            chosen[state.factor_id] = state
    for state in chosen.values():
        if state.status == DataStatus.UNAVAILABLE or state.coverage <= 0 or state.reliability <= 0:
            continue
        factor_sensitivity = sensitivity(asset_id, state.factor_id)
        if factor_sensitivity == 0:
            continue
        horizon_weight = float(meta[state.factor_id]["horizons"].get(horizon, 0.0))
        if horizon_weight == 0:
            continue
        # Reliability dampens stale / partial factors. Coverage is confidence-oriented and does not
        # double-penalize the numerical direction once a factor is considered usable.
        raw = factor_sensitivity * state.state * state.reliability * horizon_weight
        after_conflict = raw * effects.contribution_multipliers.get(state.factor_id, 1.0)
        rows.append({
            "factor_id": state.factor_id,
            "group": meta[state.factor_id]["group"],
            "factor_state": state.state,
            "sensitivity": factor_sensitivity,
            "reliability": state.reliability,
            "horizon_weight": horizon_weight,
            "raw": raw,
            "after_conflict": after_conflict,
        })
    return rows


def _apply_group_caps(rows: list[dict]) -> list[dict]:
    caps = weight_config().get("group_caps", {})
    total_abs = sum(abs(row["after_conflict"]) for row in rows) or 1.0
    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_group[row["group"]].append(row)
    for group, group_rows in by_group.items():
        cap_ratio = caps.get(group)
        group_abs = sum(abs(row["after_conflict"]) for row in group_rows)
        if cap_ratio is not None and group_abs > float(cap_ratio) * total_abs:
            scale = (float(cap_ratio) * total_abs) / group_abs
        else:
            scale = 1.0
        for row in group_rows:
            row["after_group_cap"] = row["after_conflict"] * scale
    return rows


def _direction_for_horizon(
    asset_id: str,
    states: list[FactorState],
    horizon: str,
    effects: RuleEffects,
) -> tuple[float, list[dict]]:
    rows = _apply_group_caps(_raw_horizon_contributions(asset_id, states, horizon, effects))
    numerator = sum(row["after_group_cap"] for row in rows)
    denominator = sum(
        abs(row["sensitivity"] * row["reliability"] * row["horizon_weight"] * 100.0)
        for row in rows
    ) or 1.0
    direction = _clip(100.0 * numerator / denominator, -100.0, 100.0)
    return direction, rows


def _horizon_results(
    asset_id: str,
    states: list[FactorState],
    effects: RuleEffects,
) -> dict[str, tuple[float, list[dict]]]:
    """Compute each horizon exactly once.

    Directions and contributions are derived from the same rows, and every call re-reads the
    weights/factor configs and re-applies group caps. Computing them once per asset keeps the
    numbers identical while removing a 2x duplicate pass over every scoring run.
    """
    return {
        horizon: _direction_for_horizon(asset_id, states, horizon, effects)
        for horizon in HORIZON_BLEND
    }


def _composite_contributions(
    horizon_results: dict[str, tuple[float, list[dict]]],
) -> list[FactorContribution]:
    aggregate: dict[str, dict] = {}
    for horizon, blend in HORIZON_BLEND.items():
        _, rows = horizon_results[horizon]
        for row in rows:
            slot = aggregate.setdefault(row["factor_id"], {
                "factor_id": row["factor_id"],
                "group": row["group"],
                "factor_state": row["factor_state"],
                "sensitivity": row["sensitivity"],
                "reliability": row["reliability"],
                "raw": 0.0,
                "after_conflict": 0.0,
                "after_group_cap": 0.0,
            })
            slot["raw"] += blend * row["raw"]
            slot["after_conflict"] += blend * row["after_conflict"]
            slot["after_group_cap"] += blend * row["after_group_cap"]

    contributions = [
        FactorContribution(
            factor_id=row["factor_id"],
            group=row["group"],
            factor_state=row["factor_state"],
            sensitivity=row["sensitivity"],
            reliability=row["reliability"],
            raw_contribution=row["raw"],
            after_conflict=row["after_conflict"],
            after_group_cap=row["after_group_cap"],
        )
        for row in aggregate.values()
    ]
    positives = sorted((c for c in contributions if c.after_group_cap > 0), key=lambda c: c.after_group_cap, reverse=True)
    negatives = sorted((c for c in contributions if c.after_group_cap < 0), key=lambda c: c.after_group_cap)
    for i, item in enumerate(positives, 1):
        item.rank_positive = i
    for i, item in enumerate(negatives, 1):
        item.rank_negative = i
    return contributions


def calculate_asset_score(
    asset_id: str,
    states: list[FactorState],
    effects: RuleEffects,
    confidence_factory,
    versions: VersionSnapshot | None = None,
) -> AssetScore:
    if not states:
        raise ValueError("calculate_asset_score requires at least one resolved factor state")
    assets = _asset_metadata()
    horizon_results = _horizon_results(asset_id, states, effects)
    t_dir = horizon_results[Horizon.TACTICAL.value][0]
    s_dir = horizon_results[Horizon.SWING.value][0]
    st_dir = horizon_results[Horizon.STRATEGIC.value][0]

    t_score = _clip(50 + t_dir / 2, 0, 100)
    s_score = _clip(50 + s_dir / 2, 0, 100)
    st_score = _clip(50 + st_dir / 2, 0, 100)

    direction = sum(HORIZON_BLEND[h] * value for h, value in {
        Horizon.TACTICAL.value: t_dir,
        Horizon.SWING.value: s_dir,
        Horizon.STRATEGIC.value: st_dir,
    }.items())
    final_score = _clip(50 + direction / 2, 0, 100)

    contributions = _composite_contributions(horizon_results)
    positives = sorted((c for c in contributions if c.after_group_cap > 0), key=lambda c: c.after_group_cap, reverse=True)
    negatives = sorted((c for c in contributions if c.after_group_cap < 0), key=lambda c: c.after_group_cap)

    factor_names = {k: v["name"] for k, v in _factor_metadata().items()}
    confidence = confidence_factory(asset_id, contributions, states, effects)
    return AssetScore(
        asset_score_id=uuid4(),
        asset_id=asset_id,
        asset_name=assets[asset_id]["name"],
        as_of_ts=states[0].as_of_ts,
        tactical_score=t_score,
        swing_score=s_score,
        strategic_score=st_score,
        direction_score=direction,
        score=final_score,
        label=score_label(final_score),
        confidence=confidence,
        contributions=contributions,
        top_positive=[factor_names[c.factor_id] for c in positives[:3]],
        top_negative=[factor_names[c.factor_id] for c in negatives[:3]],
        risk_flags=list(effects.risk_flags),
        versions=versions or VersionSnapshot(),
    )
