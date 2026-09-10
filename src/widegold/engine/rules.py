from dataclasses import dataclass, field

from widegold.schemas.factors import FactorState


@dataclass
class RuleEffects:
    contribution_multipliers: dict[str, float] = field(default_factory=dict)
    confidence_penalty: float = 0.0
    risk_flags: list[str] = field(default_factory=list)


def _state_values(states: list[FactorState], asset_id: str | None = None) -> dict[str, float]:
    chosen: dict[str, FactorState] = {}
    for item in states:
        if item.asset_id not in {None, asset_id}:
            continue
        current = chosen.get(item.factor_id)
        if current is None or (asset_id is not None and item.asset_id == asset_id and current.asset_id is None):
            chosen[item.factor_id] = item
    return {factor_id: state.state for factor_id, state in chosen.items()}


def evaluate_conflicts(states: list[FactorState], asset_id: str | None = None) -> RuleEffects:
    values = _state_values(states, asset_id)
    effects = RuleEffects()
    chosen: dict[str, FactorState] = {}
    for item in states:
        if item.asset_id not in {None, asset_id}:
            continue
        current = chosen.get(item.factor_id)
        if current is None or (asset_id is not None and item.asset_id == asset_id and current.asset_id is None):
            chosen[item.factor_id] = item
    if (
        values.get("EQ01_CN_FUNDING_LIQUIDITY", 0) > 40
        and values.get("EQ04_CN_GROWTH_MOMENTUM", 0) < -40
        and values.get("EQ06_INDEX_EARNINGS", 0) < -20
    ):
        effects.contribution_multipliers["EQ01_CN_FUNDING_LIQUIDITY"] = 0.60
        effects.confidence_penalty += 8.0
        effects.risk_flags.append("liquidity_without_growth_confirmation")

    if asset_id == "RMB_GOLD":
        cftc = chosen.get("G08_CFTC_POSITIONING")
        crowding = [] if cftc is None else [
            flag for flag in cftc.quality_flags if flag.startswith("cftc_long_crowding:")
        ]
        if crowding:
            effects.risk_flags.append("gold_positioning_crowded")
            try:
                crowd_level = max(float(flag.split(":", 1)[1]) for flag in crowding)
            except (ValueError, IndexError):
                crowd_level = 0.5
            effects.confidence_penalty += min(5.0, 2.0 + 3.0 * crowd_level)

    if asset_id != "RMB_GOLD":
        # Falling discount rates can reflect healthy easing or a growth scare. When growth and
        # earnings are both clearly weak, cap the positive valuation contribution rather than
        # letting lower government-bond yields mechanically dominate the equity score.
        if (
            values.get("EQ03_CN_DISCOUNT_RATE", 0) > 30
            and values.get("EQ04_CN_GROWTH_MOMENTUM", 0) < -30
            and values.get("EQ06_INDEX_EARNINGS", 0) < -20
        ):
            effects.contribution_multipliers["EQ03_CN_DISCOUNT_RATE"] = min(
                effects.contribution_multipliers.get("EQ03_CN_DISCOUNT_RATE", 1.0), 0.55
            )
            effects.risk_flags.append("falling_yields_growth_scare")
            effects.confidence_penalty += 4.0

        rmb = chosen.get("EQ09_RMB_EXTERNAL_BALANCE")
        if rmb is not None and "idiosyncratic_rmb_weakness" in rmb.quality_flags:
            effects.risk_flags.append("idiosyncratic_rmb_weakness")
            effects.confidence_penalty += 3.0

        price_profit = chosen.get("EQ05_PRICE_PROFIT_CYCLE")
        if price_profit is not None and "ppi_reflation_without_profit_confirmation" in price_profit.quality_flags:
            effects.risk_flags.append("reflation_without_profit_confirmation")
            effects.confidence_penalty += 3.0

        credit = chosen.get("EQ02_CN_CREDIT_MONEY")
        if credit is not None and any(
            flag in credit.quality_flags
            for flag in {
                "money_activation_without_credit_confirmation",
                "credit_expansion_without_money_activation",
                "credit_money_signal_divergence",
            }
        ):
            effects.risk_flags.append("credit_money_divergence")
            effects.confidence_penalty += 4.0

        growth = chosen.get("EQ04_CN_GROWTH_MOMENTUM")
        if growth is not None and "growth_signal_divergence" in growth.quality_flags:
            effects.risk_flags.append("growth_signal_divergence")
            effects.confidence_penalty += 3.0

    if asset_id == "RMB_GOLD":
        central_bank = chosen.get("G06_CENTRAL_BANK_DEMAND")
        if central_bank is not None and "global_central_bank_demand_missing_china_proxy_only" in central_bank.quality_flags:
            effects.risk_flags.append("central_bank_demand_proxy_only")
            effects.confidence_penalty += 2.0
    return effects
