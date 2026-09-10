from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from math import tanh

from widegold.domain.enums import DataStatus
from widegold.engine.indicator_features import feature_score, robust_score
from widegold.schemas.indicators import IndicatorObservation
from widegold.schemas.resilience import FactorInput
from widegold.settings.config import (
    asset_config,
    factor_calculator_config,
    factor_config,
    indicator_config,
    resilience_config,
)


@dataclass(frozen=True)
class CalculationContext:
    as_of: datetime
    histories: dict[tuple[str, str | None], list[IndicatorObservation]]

    def history(self, indicator_id: str, asset_id: str | None = None) -> list[IndicatorObservation]:
        scoped = self.histories.get((indicator_id, asset_id))
        if scoped:
            return scoped
        return self.histories.get((indicator_id, None), [])


def _factor_meta() -> dict[str, dict]:
    return {row["id"]: row for row in factor_config()["factors"]}


def _asset_ids_for_family(family: str) -> list[str]:
    return [row["id"] for row in asset_config()["assets"] if row["family"] == family]


def _indicator_meta() -> dict[str, dict]:
    return {row["id"]: row for row in indicator_config()["indicators"]}


def _observation_ts(row: IndicatorObservation, as_of: datetime) -> datetime:
    """Use the upstream observation period for freshness, not today's ingestion time.

    Public aggregation endpoints often do not expose the original historical release timestamp.
    `release_ts` therefore remains the conservative first-known-at timestamp for point-in-time
    storage, while freshness must look at the latest economic/market observation date. Otherwise a
    dead provider returning a 2024 table in 2026 would be incorrectly treated as fresh every day.
    """
    tz = as_of.tzinfo
    return datetime.combine(row.observation_date, time.max, tzinfo=tz)


def _history_freshness(
    indicator_id: str, history: list[IndicatorObservation], as_of: datetime
) -> tuple[str, float, datetime | None]:
    """Return (freshness, reliability multiplier, latest observation timestamp).

    `hard_expired` means the series is too old to be used at all. `stale` remains usable with an
    explicit reliability penalty. Thresholds are frequency-specific and configuration driven.
    """
    if not history:
        return "missing", 0.0, None
    meta = _indicator_meta().get(indicator_id, {})
    frequency = str(meta.get("frequency", "daily"))
    cfg = resilience_config().get("indicator_freshness", {})
    fresh_hours = float(cfg.get("fresh_hours", {}).get(frequency, 96))
    hard_hours = float(cfg.get("hard_max_age_hours", {}).get(frequency, max(fresh_hours * 2, 168)))
    floor = float(cfg.get("stale_reliability_floor", 0.45))
    latest = max(history, key=lambda x: x.observation_date)
    latest_ts = _observation_ts(latest, as_of)
    age_hours = max(0.0, (as_of - latest_ts).total_seconds() / 3600.0)
    if age_hours <= fresh_hours:
        return "fresh", 1.0, latest_ts
    if age_hours > hard_hours:
        return "hard_expired", 0.0, latest_ts
    span = max(1.0, hard_hours - fresh_hours)
    frac = min(1.0, (age_hours - fresh_hours) / span)
    multiplier = max(floor, 1.0 - (1.0 - floor) * frac)
    return "stale", multiplier, latest_ts


def _effective_input_history(
    indicator_id: str, history: list[IndicatorObservation], as_of: datetime
) -> tuple[list[IndicatorObservation], float, str, datetime | None]:
    freshness, multiplier, latest_ts = _history_freshness(indicator_id, history, as_of)
    if freshness in {"missing", "hard_expired"}:
        return [], 0.0, freshness, latest_ts
    return history, multiplier, freshness, latest_ts


def required_indicator_keys() -> set[tuple[str, str | None]]:
    """Return all logical indicator keys needed by the 27 V1 factor calculators."""
    cfg = factor_calculator_config()["factors"]
    result: set[tuple[str, str | None]] = set()
    factor_meta = _factor_meta()
    for factor_id, spec in cfg.items():
        if spec.get("event_only"):
            continue
        if spec.get("scope") == "asset":
            family = factor_meta[factor_id]["family"]
            assets = _asset_ids_for_family(family)
            indicator_map = spec.get("asset_indicator_map", {})
            for asset_id in assets:
                for input_spec in spec.get("inputs", []):
                    indicator_id = input_spec["indicator"]
                    if indicator_id == "$ASSET":
                        indicator_id = indicator_map.get(asset_id)
                    if indicator_id:
                        result.add((indicator_id, asset_id if input_spec.get("asset_scoped") else None))
        else:
            for input_spec in spec.get("inputs", []):
                indicator_id = input_spec["indicator"]
                if indicator_id != "$ASSET":
                    result.add((indicator_id, None))
    return result





def _single_indicator_feature_bundle(
    indicator_id: str,
    context: CalculationContext,
    *features: str,
    asset_id: str | None = None,
) -> tuple[dict[str, float], list[IndicatorObservation], float, str, datetime | None]:
    history = context.history(indicator_id, asset_id)
    history, freshness_mult, freshness, latest_ts = _effective_input_history(
        indicator_id, history, context.as_of
    )
    scores: dict[str, float] = {}
    if history:
        for feature in features:
            value = feature_score(feature, history)
            if value is not None:
                scores[feature] = float(value)
    return scores, history, freshness_mult, freshness, latest_ts


def _funding_liquidity(
    factor_id: str,
    spec: dict,
    context: CalculationContext,
    *,
    asset_id: str | None = None,
) -> FactorInput:
    """DR007 funding-liquidity state with level + fast + medium-term direction.

    A single 20-observation change is too brittle around month/quarter-end funding stress. V1.4
    uses three transparent components while preserving the rulebook direction: lower/falling DR007
    supports risk-taking, higher/rising DR007 suppresses it. Calendar seasonality is not guessed;
    unusual quarter-end moves will later be handled by a dedicated seasonal baseline when enough
    point-in-time history is available.
    """
    meta = _factor_meta()[factor_id]
    scores, history, freshness_mult, freshness, latest_ts = _single_indicator_feature_bundle(
        "CN_DR007", context, "level_robust", "change_5", "change_20"
    )
    if not history:
        return FactorInput(
            factor_id=factor_id,
            asset_id=asset_id,
            value=None,
            observed_at=latest_ts or context.as_of,
            status=DataStatus.UNAVAILABLE,
            reliability=0.0,
            warnings=[f"indicator_{freshness}:CN_DR007"],
        )

    components = [
        ("level_robust", 0.30),
        ("change_5", 0.35),
        ("change_20", 0.35),
    ]
    used = sum(weight for feature, weight in components if feature in scores)
    if used <= 0:
        return FactorInput(
            factor_id=factor_id,
            asset_id=asset_id,
            value=None,
            observed_at=latest_ts or context.as_of,
            status=DataStatus.UNAVAILABLE,
            reliability=0.0,
            source_ids=sorted({row.source_id for row in history[-3:]}),
            warnings=["insufficient_history:CN_DR007"],
        )
    # Invert all DR007 components: lower level / falling rate = positive liquidity support.
    value = sum((-scores[feature]) * weight for feature, weight in components if feature in scores) / used
    coverage = min(1.0, used)
    status = DataStatus.STALE if freshness == "stale" and coverage >= 0.8 else (
        DataStatus.VALID if coverage >= 0.8 else DataStatus.PARTIAL
    )
    reliability = (
        float(meta.get("reliability", 0.7))
        * float(spec.get("reliability_multiplier", 1.0))
        * coverage
        * freshness_mult
    )
    return FactorInput(
        factor_id=factor_id,
        asset_id=asset_id,
        value=max(-100.0, min(100.0, value)),
        observed_at=latest_ts or context.as_of,
        status=status,
        reliability=max(0.0, min(1.0, reliability)),
        source_ids=sorted({row.source_id for row in history[-3:]}),
        warnings=[
            f"funding_components:{','.join(sorted(scores))}",
            f"freshness:{freshness}",
        ],
    )



def _credit_money(
    factor_id: str,
    spec: dict,
    context: CalculationContext,
    *,
    asset_id: str | None = None,
) -> FactorInput:
    """Credit/money state with explicit confirmation and partial-data muting.

    M1 activation and social-financing expansion describe related but different transmission
    channels. A single available series therefore must not be re-normalized into a full-strength
    factor. When both are present, divergence is surfaced as a quality flag rather than hidden by
    the average.
    """
    meta = _factor_meta()[factor_id]
    m1_scores, m1_hist, m1_mult, m1_fresh, m1_ts = _single_indicator_feature_bundle(
        "CN_M1_YOY", context, "level_robust", "change_3"
    )
    tsf_scores, tsf_hist, tsf_mult, tsf_fresh, tsf_ts = _single_indicator_feature_bundle(
        "CN_TSFIN_GROWTH", context, "yoy_pct_12"
    )
    warnings: list[str] = []

    m1 = None
    if m1_scores:
        pieces = []
        if "level_robust" in m1_scores:
            pieces.append((m1_scores["level_robust"], 0.60))
        if "change_3" in m1_scores:
            pieces.append((m1_scores["change_3"], 0.40))
        if pieces:
            m1 = sum(v * w for v, w in pieces) / sum(w for _, w in pieces)
    elif not m1_hist:
        warnings.append(f"indicator_{m1_fresh}:CN_M1_YOY")

    tsf = tsf_scores.get("yoy_pct_12") if tsf_scores else None
    if tsf is None and not tsf_hist:
        warnings.append(f"indicator_{tsf_fresh}:CN_TSFIN_GROWTH")

    if m1 is None and tsf is None:
        return FactorInput(
            factor_id=factor_id,
            asset_id=asset_id,
            value=None,
            observed_at=context.as_of,
            status=DataStatus.UNAVAILABLE,
            reliability=0.0,
            warnings=warnings or ["credit_money_inputs_missing"],
        )

    # Missing channels contribute zero instead of renormalizing the surviving proxy to 100%.
    value = 0.45 * (m1 or 0.0) + 0.55 * (tsf or 0.0)
    coverage = (0.45 if m1 is not None else 0.0) + (0.55 if tsf is not None else 0.0)

    if m1 is not None and tsf is not None:
        if m1 > 30 and tsf < -30:
            value = min(value, 20.0)
            warnings.append("money_activation_without_credit_confirmation")
        elif tsf > 30 and m1 < -30:
            value = min(value, 20.0)
            warnings.append("credit_expansion_without_money_activation")
        if m1 * tsf < 0 and abs(m1 - tsf) > 70:
            warnings.append("credit_money_signal_divergence")
    else:
        # One-channel observations remain useful, but are explicitly muted.
        value = max(-35.0, min(35.0, value))
        warnings.append("credit_money_single_channel_partial")

    freshness_rows = []
    if m1 is not None:
        freshness_rows.append((m1_mult, m1_fresh, m1_ts))
    if tsf is not None:
        freshness_rows.append((tsf_mult, tsf_fresh, tsf_ts))
    freshness_mult = min(row[0] for row in freshness_rows)
    stale = any(row[1] == "stale" for row in freshness_rows)
    observed_at = min((row[2] for row in freshness_rows if row[2] is not None), default=context.as_of)
    status = DataStatus.STALE if stale and coverage >= 0.80 else (
        DataStatus.VALID if coverage >= 0.80 else DataStatus.PARTIAL
    )
    reliability = (
        float(meta.get("reliability", 0.7))
        * float(spec.get("reliability_multiplier", 1.0))
        * coverage
        * freshness_mult
    )
    sources = sorted({row.source_id for row in [*m1_hist[-3:], *tsf_hist[-3:]]})
    return FactorInput(
        factor_id=factor_id,
        asset_id=asset_id,
        value=max(-100.0, min(100.0, value)),
        observed_at=observed_at,
        status=status,
        reliability=max(0.0, min(1.0, reliability)),
        source_ids=sources,
        warnings=warnings,
    )


def _growth_momentum(
    factor_id: str,
    spec: dict,
    context: CalculationContext,
    *,
    asset_id: str | None = None,
) -> FactorInput:
    """Broad growth state from PMI, industrial production and exports.

    The factor deliberately keeps missing channels at zero weight in the *total* composite rather
    than renormalizing one strong release to a full growth signal. PMI is the leading component;
    industrial production and exports confirm realised activity and external demand.
    """
    meta = _factor_meta()[factor_id]
    definitions = [
        ("CN_PMI_MANUFACTURING", ("pmi_state",), 0.45),
        ("CN_INDUSTRIAL_PRODUCTION_YOY", ("level_robust", "change_3"), 0.35),
        ("CN_EXPORTS_YOY", ("level_robust", "change_3"), 0.20),
    ]
    values: dict[str, float] = {}
    freshness: list[tuple[float, str, datetime | None]] = []
    sources: set[str] = set()
    warnings: list[str] = []
    coverage = 0.0

    for indicator_id, features, nominal_weight in definitions:
        scores, history, mult, state, ts = _single_indicator_feature_bundle(
            indicator_id, context, *features
        )
        component = None
        if scores:
            if indicator_id == "CN_PMI_MANUFACTURING":
                component = scores.get("pmi_state")
            else:
                available = [(scores[k], 0.55 if k == "level_robust" else 0.45) for k in features if k in scores]
                if available:
                    component = sum(v * w for v, w in available) / sum(w for _, w in available)
        if component is None:
            warnings.append(f"indicator_{state if not history else 'insufficient_history'}:{indicator_id}")
            continue
        values[indicator_id] = float(component)
        coverage += nominal_weight
        freshness.append((mult, state, ts))
        sources.update(row.source_id for row in history[-3:])

    if not values:
        return FactorInput(
            factor_id=factor_id,
            asset_id=asset_id,
            value=None,
            observed_at=context.as_of,
            status=DataStatus.UNAVAILABLE,
            reliability=0.0,
            warnings=warnings or ["growth_inputs_missing"],
        )

    value = (
        0.45 * values.get("CN_PMI_MANUFACTURING", 0.0)
        + 0.35 * values.get("CN_INDUSTRIAL_PRODUCTION_YOY", 0.0)
        + 0.20 * values.get("CN_EXPORTS_YOY", 0.0)
    )
    if coverage < 0.80:
        value = max(-45.0, min(45.0, value))
        warnings.append("growth_signal_partial")
    if len(values) >= 2:
        signs = [1 if v > 25 else -1 if v < -25 else 0 for v in values.values()]
        if 1 in signs and -1 in signs:
            warnings.append("growth_signal_divergence")
            value = max(-35.0, min(35.0, value))

    freshness_mult = min(row[0] for row in freshness)
    stale = any(row[1] == "stale" for row in freshness)
    observed_at = min((row[2] for row in freshness if row[2] is not None), default=context.as_of)
    status = DataStatus.STALE if stale and coverage >= 0.80 else (
        DataStatus.VALID if coverage >= 0.80 else DataStatus.PARTIAL
    )
    reliability = (
        float(meta.get("reliability", 0.7))
        * float(spec.get("reliability_multiplier", 1.0))
        * coverage
        * freshness_mult
    )
    return FactorInput(
        factor_id=factor_id,
        asset_id=asset_id,
        value=max(-100.0, min(100.0, value)),
        observed_at=observed_at,
        status=status,
        reliability=max(0.0, min(1.0, reliability)),
        source_ids=sorted(sources),
        warnings=warnings,
    )


def _central_bank_gold_demand(
    factor_id: str,
    spec: dict,
    context: CalculationContext,
    *,
    asset_id: str | None = None,
) -> FactorInput:
    """Global official-sector demand with China reserve changes as a bounded fallback only."""
    meta = _factor_meta()[factor_id]
    global_scores, global_hist, global_mult, global_fresh, global_ts = _single_indicator_feature_bundle(
        "GLOBAL_CENTRAL_BANK_GOLD", context, "level_robust"
    )
    china_scores, china_hist, china_mult, china_fresh, china_ts = _single_indicator_feature_bundle(
        "CN_OFFICIAL_GOLD_RESERVE", context, "change_3"
    )
    global_value = global_scores.get("level_robust") if global_scores else None
    china_value = china_scores.get("change_3") if china_scores else None
    warnings: list[str] = []

    if global_value is None and china_value is None:
        if not global_hist:
            warnings.append(f"indicator_{global_fresh}:GLOBAL_CENTRAL_BANK_GOLD")
        if not china_hist:
            warnings.append(f"indicator_{china_fresh}:CN_OFFICIAL_GOLD_RESERVE")
        return FactorInput(
            factor_id=factor_id, asset_id=asset_id, value=None, observed_at=context.as_of,
            status=DataStatus.UNAVAILABLE, reliability=0.0,
            warnings=warnings or ["central_bank_demand_inputs_missing"],
        )

    if global_value is not None:
        # Global series defines the factor. China is only a modest confirmation channel.
        value = 0.85 * global_value + 0.15 * (china_value or 0.0)
        coverage = 1.0 if china_value is not None else 0.85
        if china_value is None:
            warnings.append("china_official_gold_confirmation_missing")
    else:
        # A single country's reserve change cannot stand in for global central-bank demand.
        value = max(-25.0, min(25.0, 0.15 * float(china_value)))
        coverage = 0.15
        warnings.append("global_central_bank_demand_missing_china_proxy_only")

    freshness = []
    if global_value is not None:
        freshness.append((global_mult, global_fresh, global_ts))
    if china_value is not None:
        freshness.append((china_mult, china_fresh, china_ts))
    freshness_mult = min(row[0] for row in freshness)
    stale = any(row[1] == "stale" for row in freshness)
    observed_at = min((row[2] for row in freshness if row[2] is not None), default=context.as_of)
    status = DataStatus.STALE if stale and coverage >= 0.80 else (
        DataStatus.VALID if coverage >= 0.80 else DataStatus.PARTIAL
    )
    reliability = (
        float(meta.get("reliability", 0.7))
        * float(spec.get("reliability_multiplier", 1.0))
        * coverage
        * freshness_mult
    )
    sources = sorted({row.source_id for row in [*global_hist[-3:], *china_hist[-3:]]})
    return FactorInput(
        factor_id=factor_id, asset_id=asset_id, value=max(-100.0, min(100.0, value)),
        observed_at=observed_at, status=status,
        reliability=max(0.0, min(1.0, reliability)), source_ids=sources, warnings=warnings,
    )


def _rmb_external_balance(
    factor_id: str,
    spec: dict,
    context: CalculationContext,
    *,
    asset_id: str | None = None,
) -> FactorInput:
    """China-specific FX pressure rather than a mechanical USD/CNY direction rule.

    A rising USD/CNY can be mostly a broad-dollar move already represented by EQ10. This factor
    therefore isolates the residual RMB move relative to the broad dollar and treats that residual
    as a proxy for China-specific external-balance / capital-flow pressure. It remains a modest,
    lower-reliability factor because the true causal reason still requires event/macro context.
    """
    meta = _factor_meta()[factor_id]
    rmb_scores, rmb_hist, rmb_mult, rmb_fresh, rmb_ts = _single_indicator_feature_bundle(
        "USDCNY", context, "change_20"
    )
    usd_scores, usd_hist, usd_mult, usd_fresh, usd_ts = _single_indicator_feature_bundle(
        "US_BROAD_DOLLAR", context, "change_20"
    )
    rmb_move = rmb_scores.get("change_20") if rmb_scores else None
    usd_move = usd_scores.get("change_20") if usd_scores else None
    warnings: list[str] = []

    if rmb_move is None:
        return FactorInput(
            factor_id=factor_id, asset_id=asset_id, value=None,
            observed_at=rmb_ts or context.as_of, status=DataStatus.UNAVAILABLE,
            reliability=0.0, warnings=[f"indicator_{rmb_fresh}:USDCNY"],
        )

    if usd_move is None:
        # Without the global-dollar control, retain only a muted directional proxy.
        value = max(-30.0, min(30.0, -0.35 * rmb_move))
        coverage = 0.55
        freshness_mult = rmb_mult
        stale = rmb_fresh == "stale"
        observed_at = rmb_ts or context.as_of
        warnings.append(f"broad_dollar_control_{usd_fresh}")
        warnings.append("rmb_direction_only_partial_proxy")
        sources = {row.source_id for row in rmb_hist[-3:]}
    else:
        # Both features are robust scores, so subtraction is an interpretable *relative stress*
        # signal rather than a regression beta. Broad USD strength is mostly left to EQ10.
        residual_pressure = rmb_move - 0.65 * usd_move
        value = max(-70.0, min(70.0, -residual_pressure))
        coverage = 1.0
        freshness_mult = min(rmb_mult, usd_mult)
        stale = rmb_fresh == "stale" or usd_fresh == "stale"
        observed_at = min((x for x in (rmb_ts, usd_ts) if x is not None), default=context.as_of)
        sources = {row.source_id for row in [*rmb_hist[-3:], *usd_hist[-3:]]}
        warnings.append(f"rmb_residual_pressure:{residual_pressure:.2f}")
        if residual_pressure > 45:
            warnings.append("idiosyncratic_rmb_weakness")
        elif residual_pressure < -45:
            warnings.append("idiosyncratic_rmb_strength")

    status = DataStatus.STALE if stale and coverage >= 0.80 else (
        DataStatus.VALID if coverage >= 0.80 else DataStatus.PARTIAL
    )
    reliability = (
        float(meta.get("reliability", 0.7))
        * float(spec.get("reliability_multiplier", 1.0))
        * coverage
        * freshness_mult
    )
    return FactorInput(
        factor_id=factor_id, asset_id=asset_id,
        value=max(-100.0, min(100.0, value)), observed_at=observed_at,
        status=status, reliability=max(0.0, min(1.0, reliability)),
        source_ids=sorted(sources), warnings=warnings,
    )

def _price_profit_cycle(
    factor_id: str,
    spec: dict,
    context: CalculationContext,
    *,
    asset_id: str | None = None,
) -> FactorInput:
    """Profit-first price/profit interaction.

    PPI reflation is only supportive when the profit cycle confirms it. When industrial profit is
    available it dominates the factor; a rising PPI cannot mechanically overwhelm a clearly weak
    profit state. If profit data is missing, PPI may provide a low-confidence PARTIAL state rather
    than pretending the complete price-profit cycle is observed.
    """
    meta = _factor_meta()[factor_id]
    ppi_scores, ppi_hist, ppi_mult, ppi_fresh, ppi_ts = _single_indicator_feature_bundle(
        "CN_PPI_YOY", context, "level_robust", "change_3"
    )
    profit_scores, profit_hist, profit_mult, profit_fresh, profit_ts = _single_indicator_feature_bundle(
        "CN_INDUSTRIAL_PROFIT_YOY", context, "level_robust", "change_3"
    )
    warnings: list[str] = []
    if not ppi_hist:
        warnings.append(f"indicator_{ppi_fresh}:CN_PPI_YOY")
    if not profit_hist:
        warnings.append(f"indicator_{profit_fresh}:CN_INDUSTRIAL_PROFIT_YOY")

    ppi = None
    if ppi_scores:
        pieces = []
        if "level_robust" in ppi_scores:
            pieces.append((ppi_scores["level_robust"], 0.40))
        if "change_3" in ppi_scores:
            pieces.append((ppi_scores["change_3"], 0.60))
        if pieces:
            ppi = sum(v * w for v, w in pieces) / sum(w for _, w in pieces)

    profit = None
    if profit_scores:
        pieces = []
        if "level_robust" in profit_scores:
            pieces.append((profit_scores["level_robust"], 0.65))
        if "change_3" in profit_scores:
            pieces.append((profit_scores["change_3"], 0.35))
        if pieces:
            profit = sum(v * w for v, w in pieces) / sum(w for _, w in pieces)

    if ppi is None and profit is None:
        return FactorInput(
            factor_id=factor_id,
            asset_id=asset_id,
            value=None,
            observed_at=context.as_of,
            status=DataStatus.UNAVAILABLE,
            reliability=0.0,
            warnings=warnings or ["price_profit_inputs_missing"],
        )

    if profit is not None:
        # Profits are the primary economic outcome. PPI is a smaller confirmation channel.
        value = 0.72 * profit + 0.28 * (ppi if ppi is not None else 0.0)
        coverage = 1.0 if ppi is not None else 0.72
        # Reflation without profit confirmation must not look like a strong positive regime.
        if ppi is not None and ppi > 35 and profit < -20:
            value = min(value, 10.0)
            warnings.append("ppi_reflation_without_profit_confirmation")
        if ppi is not None and ppi < -35 and profit > 25:
            # Strong profit resilience can offset deflation pressure, but keep the result bounded.
            value = max(value, -10.0)
            warnings.append("profit_resilience_offsets_ppi_weakness")
    else:
        # PPI-only is explicitly partial and muted.
        value = max(-35.0, min(35.0, float(ppi)))
        coverage = 0.28
        warnings.append("profit_data_missing_ppi_only_proxy")

    freshness_candidates = []
    if ppi is not None:
        freshness_candidates.append((ppi_mult, ppi_fresh, ppi_ts))
    if profit is not None:
        freshness_candidates.append((profit_mult, profit_fresh, profit_ts))
    freshness_mult = min(x[0] for x in freshness_candidates)
    stale = any(x[1] == "stale" for x in freshness_candidates)
    observed_at = min((x[2] for x in freshness_candidates if x[2] is not None), default=context.as_of)
    if stale and coverage >= 0.80:
        status = DataStatus.STALE
    else:
        status = DataStatus.VALID if coverage >= 0.80 else DataStatus.PARTIAL
    reliability = (
        float(meta.get("reliability", 0.7))
        * float(spec.get("reliability_multiplier", 1.0))
        * coverage
        * freshness_mult
    )
    sources = sorted({row.source_id for row in [*ppi_hist[-3:], *profit_hist[-3:]]})
    return FactorInput(
        factor_id=factor_id,
        asset_id=asset_id,
        value=max(-100.0, min(100.0, value)),
        observed_at=observed_at,
        status=status,
        reliability=max(0.0, min(1.0, reliability)),
        source_ids=sources,
        warnings=warnings,
    )


def _cftc_positioning(
    factor_id: str,
    spec: dict,
    context: CalculationContext,
    *,
    asset_id: str | None = None,
) -> FactorInput:
    """CFTC gold positioning with an explicit crowding penalty.

    Improving managed/non-commercial positioning is a tactical confirmation, but an already
    extreme long level should not keep adding linearly. At high positive level scores the positive
    contribution is compressed and a crowding warning is emitted. The warning is intentionally
    retained in FactorInput so future RiskEngine versions can elevate it to a dedicated risk flag.
    """
    meta = _factor_meta()[factor_id]
    scores, history, freshness_mult, freshness, latest_ts = _single_indicator_feature_bundle(
        "CFTC_GOLD_NET_LONG", context, "change_4", "level_robust"
    )
    if not history or not scores:
        return FactorInput(
            factor_id=factor_id,
            asset_id=asset_id,
            value=None,
            observed_at=latest_ts or context.as_of,
            status=DataStatus.UNAVAILABLE,
            reliability=0.0,
            warnings=[f"indicator_{freshness}:CFTC_GOLD_NET_LONG"],
        )
    change = scores.get("change_4")
    level = scores.get("level_robust")
    pieces = []
    if change is not None:
        pieces.append((change, 0.65))
    if level is not None:
        pieces.append((level, 0.35))
    used = sum(w for _, w in pieces)
    value = sum(v * w for v, w in pieces) / used
    warnings: list[str] = []

    if level is not None and level >= 65:
        crowd = min(1.0, (level - 65.0) / 35.0)
        if value > 0:
            value *= max(0.25, 1.0 - 0.75 * crowd)
        value -= 15.0 * crowd
        warnings.append(f"cftc_long_crowding:{crowd:.3f}")
    elif level is not None and level <= -75 and change is not None and change > 20:
        # A deeply under-owned market with improving positioning can receive a modest reversal
        # confirmation, but never enough to dominate rates/USD fundamentals.
        value += min(12.0, 0.15 * change)
        warnings.append("cftc_short_covering_confirmation")

    coverage = min(1.0, used)
    status = DataStatus.STALE if freshness == "stale" and coverage >= 0.8 else (
        DataStatus.VALID if coverage >= 0.8 else DataStatus.PARTIAL
    )
    reliability = (
        float(meta.get("reliability", 0.7))
        * float(spec.get("reliability_multiplier", 1.0))
        * coverage
        * freshness_mult
    )
    return FactorInput(
        factor_id=factor_id,
        asset_id=asset_id,
        value=max(-85.0, min(85.0, value)),
        observed_at=latest_ts or context.as_of,
        status=status,
        reliability=max(0.0, min(1.0, reliability)),
        source_ids=sorted({row.source_id for row in history[-3:]}),
        warnings=[*warnings, f"freshness:{freshness}"],
    )


def _equity_erp(
    factor_id: str,
    spec: dict,
    context: CalculationContext,
    *,
    asset_id: str | None = None,
) -> FactorInput:
    """Asset-scoped equity-risk-premium proxy.

    ERP ~= earnings yield (100 / rolling PE) - China 10Y government-bond yield. The calculation
    uses only observations available in the point-in-time store and aligns each PE observation to
    the latest government-bond observation on or before that date.
    """
    meta = _factor_meta()[factor_id]
    pe = context.history("CN_INDEX_PE", asset_id)
    rate = context.history("CN_CGB_10Y")
    pe, pe_mult, pe_freshness, pe_ts = _effective_input_history("CN_INDEX_PE", pe, context.as_of)
    rate, rate_mult, rate_freshness, rate_ts = _effective_input_history("CN_CGB_10Y", rate, context.as_of)
    warnings: list[str] = []
    if not pe:
        warnings.append(f"CN_INDEX_PE:{pe_freshness}")
    if not rate:
        warnings.append(f"CN_CGB_10Y:{rate_freshness}")
    if not pe or not rate:
        return FactorInput(
            factor_id=factor_id, asset_id=asset_id, value=None, observed_at=min([x for x in (pe_ts, rate_ts) if x], default=context.as_of),
            status=DataStatus.UNAVAILABLE, reliability=0.0, warnings=warnings or ["erp_inputs_missing"],
        )

    rates = sorted(rate, key=lambda x: x.observation_date)
    pairs: list[tuple[float, IndicatorObservation, IndicatorObservation]] = []
    j = 0
    latest_rate: IndicatorObservation | None = None
    for pe_row in sorted(pe, key=lambda x: x.observation_date):
        while j < len(rates) and rates[j].observation_date <= pe_row.observation_date:
            latest_rate = rates[j]
            j += 1
        if latest_rate is None or pe_row.value <= 0:
            continue
        erp = 100.0 / float(pe_row.value) - float(latest_rate.value)
        pairs.append((erp, pe_row, latest_rate))
    if len(pairs) < 3:
        return FactorInput(
            factor_id=factor_id, asset_id=asset_id, value=None, observed_at=min(pe_ts, rate_ts),
            status=DataStatus.UNAVAILABLE, reliability=0.0, warnings=[*warnings, "insufficient_erp_history"],
        )

    erps = [x[0] for x in pairs[-120:]]
    score = robust_score(erps[-1], erps[:-1] or erps)
    freshness_mult = min(pe_mult, rate_mult)
    stale = pe_freshness == "stale" or rate_freshness == "stale"
    status = DataStatus.STALE if stale else DataStatus.VALID
    reliability = float(meta.get("reliability", 0.7)) * float(spec.get("reliability_multiplier", 1.0)) * freshness_mult
    observed_at = min(pe_ts, rate_ts)
    latest_erp = erps[-1]
    return FactorInput(
        factor_id=factor_id, asset_id=asset_id, value=max(-100.0, min(100.0, score)),
        observed_at=observed_at, status=status, reliability=max(0.0, min(1.0, reliability)),
        source_ids=sorted({pairs[-1][1].source_id, pairs[-1][2].source_id}),
        warnings=[*warnings, f"erp_pct_points:{latest_erp:.3f}", f"freshness:{pe_freshness}/{rate_freshness}"],
    )


def _gold_china_premium(
    factor_id: str,
    spec: dict,
    context: CalculationContext,
    *,
    asset_id: str | None = None,
) -> FactorInput:
    """China gold local-demand overlay reconstructed from raw auditable series.

    Theoretical RMB/gram ~= USD gold (USD/troy oz) * USD/CNY / 31.1034768.
    A positive SGE Au99.99 premium is treated as local-demand support. This factor is a
    China overlay only and does not alter the global-gold core.
    """
    meta = _factor_meta()[factor_id]
    sge_raw = context.history("SGE_AU9999")
    usd_gold_raw = context.history("GOLD_USD")
    fx_raw = context.history("USDCNY")
    sge, sge_mult, sge_fresh, sge_ts = _effective_input_history("SGE_AU9999", sge_raw, context.as_of)
    usd_gold, gold_mult, gold_fresh, gold_ts = _effective_input_history("GOLD_USD", usd_gold_raw, context.as_of)
    fx, fx_mult, fx_fresh, fx_ts = _effective_input_history("USDCNY", fx_raw, context.as_of)
    missing = [
        key for key, rows in (("SGE_AU9999", sge), ("GOLD_USD", usd_gold), ("USDCNY", fx)) if not rows
    ]
    if missing:
        return FactorInput(
            factor_id=factor_id,
            asset_id=asset_id,
            value=None,
            observed_at=context.as_of,
            status=DataStatus.UNAVAILABLE,
            reliability=0.0,
            warnings=[f"insufficient_history:{x}" for x in missing],
        )

    latest_sge = sge[-1]
    # FX and COMEX may have different market calendars. Use the latest point known as-of rather
    # than force an exact-date join that would make holidays/weekends look unavailable.
    latest_gold = usd_gold[-1]
    latest_fx = fx[-1]
    theoretical = float(latest_gold.value) * float(latest_fx.value) / 31.1034768
    if theoretical <= 0:
        return FactorInput(
            factor_id=factor_id,
            asset_id=asset_id,
            value=None,
            observed_at=context.as_of,
            status=DataStatus.UNAVAILABLE,
            reliability=0.0,
            warnings=["invalid_theoretical_rmb_gold_price"],
        )
    premium_pct = 100.0 * (float(latest_sge.value) / theoretical - 1.0)
    premium_score = 100.0 * tanh(premium_pct / 3.0)
    trend = feature_score("return_20", sge)
    used = 0.75
    value = 0.75 * premium_score
    warnings: list[str] = []
    if trend is not None:
        value += 0.25 * trend
        used += 0.25
    else:
        warnings.append("insufficient_history:SGE_AU9999:return_20")

    source_ids = sorted({latest_sge.source_id, latest_gold.source_id, latest_fx.source_id})
    observed_at = min(x for x in (sge_ts, gold_ts, fx_ts) if x is not None)
    freshness_mult = min(sge_mult, gold_mult, fx_mult)
    stale = "stale" in {sge_fresh, gold_fresh, fx_fresh}
    reliability = float(meta.get("reliability", 0.7)) * float(spec.get("reliability_multiplier", 1.0)) * used * freshness_mult
    status = DataStatus.STALE if stale and used >= 0.99 else DataStatus.VALID if used >= 0.99 else DataStatus.PARTIAL
    return FactorInput(
        factor_id=factor_id,
        asset_id=asset_id,
        value=max(-100.0, min(100.0, value / used)),
        observed_at=observed_at,
        status=status,
        reliability=max(0.0, min(1.0, reliability)),
        source_ids=source_ids,
        warnings=[
            *warnings, f"sge_premium_pct:{premium_pct:.3f}",
            f"freshness:{sge_fresh}/{gold_fresh}/{fx_fresh}",
        ],
    )

def _gold_inflation_conditional(
    factor_id: str,
    spec: dict,
    context: CalculationContext,
    *,
    asset_id: str | None = None,
) -> FactorInput:
    """Conditional gold inflation interaction from the V1 rulebook.

    Inflation is deliberately NOT a linear standalone gold signal. A rising CPI
    state only becomes meaningfully positive when real-yield and/or USD
    opportunity-cost conditions confirm it. If both move against gold, the
    interaction can be negative. When inflation is not meaningfully rising, this
    factor stays near neutral so G01/G02 carry their own independent signals.
    """
    meta = _factor_meta()[factor_id]
    total_weight = sum(abs(float(row.get("weight", 1.0))) for row in spec.get("inputs", [])) or 1.0
    used_weight = 0.0
    scores: dict[str, float] = {}
    source_ids: set[str] = set()
    warnings: list[str] = []
    observed_at = None

    for input_spec in spec.get("inputs", []):
        indicator_id = input_spec["indicator"]
        history = context.history(indicator_id, asset_id if input_spec.get("asset_scoped") else None)
        history, freshness_mult, freshness, latest_obs_ts = _effective_input_history(
            indicator_id, history, context.as_of
        )
        raw = feature_score(input_spec["feature"], history) if history else None
        if raw is None:
            warnings.append(f"indicator_{freshness}:{indicator_id}" if not history else f"insufficient_history:{indicator_id}")
            continue
        oriented = float(input_spec.get("orientation", 1.0)) * raw
        scores[indicator_id] = oriented
        used_weight += abs(float(input_spec.get("weight", 1.0))) * freshness_mult
        source_ids.update(row.source_id for row in history[-3:])
        if latest_obs_ts is not None and (observed_at is None or latest_obs_ts < observed_at):
            observed_at = latest_obs_ts
        if freshness == "stale":
            warnings.append(f"indicator_stale:{indicator_id}")

    coverage = min(1.0, used_weight / total_weight)
    inflation = scores.get("US_CPI_YOY")
    if inflation is None:
        return FactorInput(
            factor_id=factor_id, asset_id=asset_id, value=None, observed_at=context.as_of,
            status=DataStatus.UNAVAILABLE, reliability=0.0, source_ids=sorted(source_ids),
            warnings=[*warnings, "gold_inflation_requires_cpi"],
        )

    real_support = scores.get("US_REAL_YIELD_10Y", 0.0)
    usd_support = scores.get("US_BROAD_DOLLAR", 0.0)

    if inflation > 15.0:
        if real_support > 10.0 and usd_support > 5.0:
            # Inflation pressure + falling real yield + weaker USD: confirmed gold-positive regime.
            value = 0.45 * inflation + 0.35 * real_support + 0.20 * usd_support
        elif real_support < -10.0 and usd_support < -5.0:
            # Inflation surprise accompanied by rising real yields and a stronger dollar: do not
            # mechanically call it gold-positive.
            value = 0.10 * inflation + 0.55 * real_support + 0.35 * usd_support
        else:
            # Mixed macro transmission: keep the interaction intentionally muted.
            value = 0.10 * inflation + 0.20 * real_support + 0.15 * usd_support
            value = max(-35.0, min(35.0, value))
    else:
        # Falling/stable inflation is not an independent gold directional signal here; real yield
        # and USD remain represented by G01/G02 and must not be counted again through G05.
        value = 0.0

    has_stale_input = any(w.startswith("indicator_stale:") for w in warnings)
    if coverage >= 0.80 and has_stale_input:
        status = DataStatus.STALE
    else:
        status = DataStatus.VALID if coverage >= 0.80 else DataStatus.PARTIAL
    reliability = float(meta.get("reliability", 0.7)) * float(spec.get("reliability_multiplier", 1.0)) * coverage
    return FactorInput(
        factor_id=factor_id, asset_id=asset_id, value=max(-100.0, min(100.0, value)),
        observed_at=observed_at or context.as_of, status=status,
        reliability=max(0.0, min(1.0, reliability)), source_ids=sorted(source_ids), warnings=warnings,
    )

def _calculate_one(
    factor_id: str,
    spec: dict,
    context: CalculationContext,
    *,
    asset_id: str | None = None,
) -> FactorInput:
    meta = _factor_meta()[factor_id]
    if spec.get("conditional") == "funding_liquidity":
        return _funding_liquidity(factor_id, spec, context, asset_id=asset_id)
    if spec.get("conditional") == "credit_money":
        return _credit_money(factor_id, spec, context, asset_id=asset_id)
    if spec.get("conditional") == "growth_momentum":
        return _growth_momentum(factor_id, spec, context, asset_id=asset_id)
    if spec.get("conditional") == "rmb_external_balance":
        return _rmb_external_balance(factor_id, spec, context, asset_id=asset_id)
    if spec.get("conditional") == "price_profit_cycle":
        return _price_profit_cycle(factor_id, spec, context, asset_id=asset_id)
    if spec.get("conditional") == "cftc_positioning":
        return _cftc_positioning(factor_id, spec, context, asset_id=asset_id)
    if spec.get("conditional") == "central_bank_gold_demand":
        return _central_bank_gold_demand(factor_id, spec, context, asset_id=asset_id)
    if spec.get("conditional") == "equity_erp":
        return _equity_erp(factor_id, spec, context, asset_id=asset_id)
    if spec.get("conditional") == "gold_inflation":
        return _gold_inflation_conditional(factor_id, spec, context, asset_id=asset_id)
    if spec.get("conditional") == "gold_china_premium":
        return _gold_china_premium(factor_id, spec, context, asset_id=asset_id)
    if spec.get("event_only"):
        # Event-only factors are intentionally absent here; factor resolution will create
        # neutral or event-driven states according to factor_runtime.yaml.
        return FactorInput(
            factor_id=factor_id,
            asset_id=asset_id,
            value=None,
            observed_at=context.as_of,
            status=DataStatus.UNAVAILABLE,
            reliability=0.0,
            warnings=["event_only_factor"],
        )

    total_weight = sum(abs(float(row.get("weight", 1.0))) for row in spec.get("inputs", [])) or 1.0
    used_weight = 0.0
    weighted_score = 0.0
    source_ids: set[str] = set()
    observed_at = None
    warnings: list[str] = []
    indicator_map = spec.get("asset_indicator_map", {})

    for input_spec in spec.get("inputs", []):
        indicator_id = input_spec["indicator"]
        if indicator_id == "$ASSET":
            indicator_id = indicator_map.get(asset_id)
        if not indicator_id:
            warnings.append(f"missing_indicator_mapping:{asset_id}")
            continue
        history_asset = asset_id if input_spec.get("asset_scoped") else None
        history = context.history(indicator_id, history_asset)
        history, freshness_mult, freshness, latest_obs_ts = _effective_input_history(
            indicator_id, history, context.as_of
        )
        if not history:
            warnings.append(f"indicator_{freshness}:{indicator_id}")
            continue
        score = feature_score(input_spec["feature"], history)
        if score is None:
            warnings.append(f"insufficient_history:{indicator_id}")
            continue
        base_weight = abs(float(input_spec.get("weight", 1.0)))
        effective_weight = base_weight * freshness_mult
        orientation = float(input_spec.get("orientation", 1.0))
        weighted_score += effective_weight * orientation * score
        used_weight += effective_weight
        source_ids.update(row.source_id for row in history[-3:])
        if latest_obs_ts is not None and (observed_at is None or latest_obs_ts < observed_at):
            # Oldest contributing component governs factor freshness.
            observed_at = latest_obs_ts
        if freshness == "stale":
            warnings.append(f"indicator_stale:{indicator_id}")

    coverage = min(1.0, used_weight / total_weight)
    if coverage <= 0:
        return FactorInput(
            factor_id=factor_id,
            asset_id=asset_id,
            value=None,
            observed_at=context.as_of,
            status=DataStatus.UNAVAILABLE,
            reliability=0.0,
            source_ids=sorted(source_ids),
            warnings=warnings or ["no_indicator_data"],
        )

    value = weighted_score / used_weight
    if spec.get("saturating"):
        value = max(-85.0, min(85.0, value))

    has_stale_input = any(w.startswith("indicator_stale:") for w in warnings)
    if coverage >= 0.80 and has_stale_input:
        status = DataStatus.STALE
    else:
        status = DataStatus.VALID if coverage >= 0.80 else DataStatus.PARTIAL
    reliability = float(meta.get("reliability", 0.7)) * float(spec.get("reliability_multiplier", 1.0)) * coverage
    return FactorInput(
        factor_id=factor_id,
        asset_id=asset_id,
        value=max(-100.0, min(100.0, value)),
        observed_at=observed_at or context.as_of,
        status=status,
        reliability=max(0.0, min(1.0, reliability)),
        source_ids=sorted(source_ids),
        warnings=warnings,
    )


def calculate_all_factor_inputs(
    histories: dict[tuple[str, str | None], list[IndicatorObservation]],
    as_of: datetime,
) -> list[FactorInput]:
    context = CalculationContext(as_of=as_of, histories=histories)
    cfg = factor_calculator_config()["factors"]
    meta = _factor_meta()
    rows: list[FactorInput] = []
    for factor_id, spec in cfg.items():
        if spec.get("scope") == "asset":
            for asset_id in _asset_ids_for_family(meta[factor_id]["family"]):
                rows.append(_calculate_one(factor_id, spec, context, asset_id=asset_id))
        else:
            rows.append(_calculate_one(factor_id, spec, context))
    return rows
