from datetime import datetime, timezone

from widegold.engine.rules import evaluate_conflicts
from widegold.schemas.factors import FactorState, FactorStateComponents


def _state(factor_id: str, value: float):
    return FactorState(
        factor_id=factor_id,
        as_of_ts=datetime.now(timezone.utc),
        state=value,
        components=FactorStateComponents(level=value),
        reliability=0.9,
        coverage=1.0,
    )


def test_liquidity_without_growth_rule():
    effects = evaluate_conflicts([
        _state("EQ01_CN_FUNDING_LIQUIDITY", 60),
        _state("EQ04_CN_GROWTH_MOMENTUM", -55),
        _state("EQ06_INDEX_EARNINGS", -35),
    ])
    assert effects.contribution_multipliers["EQ01_CN_FUNDING_LIQUIDITY"] == 0.60
    assert effects.confidence_penalty == 8
    assert "liquidity_without_growth_confirmation" in effects.risk_flags


def test_gold_cftc_crowding_becomes_risk_flag():
    state = FactorState(
        factor_id="G08_CFTC_POSITIONING",
        as_of_ts=datetime.now(timezone.utc),
        state=55,
        components=FactorStateComponents(level=55),
        reliability=0.7,
        coverage=1.0,
        quality_flags=["cftc_long_crowding:0.800"],
    )
    effects = evaluate_conflicts([state], asset_id="RMB_GOLD")
    assert "gold_positioning_crowded" in effects.risk_flags
    assert effects.confidence_penalty > 0


def test_price_profit_warning_becomes_equity_risk_flag():
    state = FactorState(
        factor_id="EQ05_PRICE_PROFIT_CYCLE",
        as_of_ts=datetime.now(timezone.utc),
        state=-15,
        components=FactorStateComponents(level=-15),
        reliability=0.7,
        coverage=1.0,
        quality_flags=["ppi_reflation_without_profit_confirmation"],
    )
    effects = evaluate_conflicts([state], asset_id="CSI300")
    assert "reflation_without_profit_confirmation" in effects.risk_flags
    assert effects.confidence_penalty == 3.0



def test_credit_money_divergence_reaches_equity_risk_flags():
    from widegold.schemas.factors import FactorState, FactorStateComponents
    from widegold.domain.enums import DataStatus
    from datetime import datetime, timezone

    state = FactorState(
        factor_id="EQ02_CN_CREDIT_MONEY", as_of_ts=datetime.now(timezone.utc), state=5,
        components=FactorStateComponents(level=5), reliability=.7, coverage=1,
        status=DataStatus.VALID, quality_flags=["credit_money_signal_divergence"],
    )
    effects = evaluate_conflicts([state], "CSI300")
    assert "credit_money_divergence" in effects.risk_flags
    assert effects.confidence_penalty >= 4


def test_central_bank_china_only_proxy_reaches_gold_risk_flags():
    from widegold.schemas.factors import FactorState, FactorStateComponents
    from widegold.domain.enums import DataStatus
    from datetime import datetime, timezone

    state = FactorState(
        factor_id="G06_CENTRAL_BANK_DEMAND", as_of_ts=datetime.now(timezone.utc), state=8,
        components=FactorStateComponents(level=8), reliability=.2, coverage=.15,
        status=DataStatus.PARTIAL, quality_flags=["global_central_bank_demand_missing_china_proxy_only"],
    )
    effects = evaluate_conflicts([state], "RMB_GOLD")
    assert "central_bank_demand_proxy_only" in effects.risk_flags



def test_falling_yield_support_is_capped_during_growth_and_earnings_scare():
    from widegold.schemas.factors import FactorState, FactorStateComponents
    from widegold.domain.enums import DataStatus
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    def state(fid, value):
        return FactorState(
            factor_id=fid, asset_id=None, as_of_ts=now, state=value,
            components=FactorStateComponents(level=value), reliability=.8, coverage=1,
            status=DataStatus.VALID,
        )
    effects = evaluate_conflicts([
        state("EQ03_CN_DISCOUNT_RATE", 55),
        state("EQ04_CN_GROWTH_MOMENTUM", -50),
        state("EQ06_INDEX_EARNINGS", -35),
    ], "CSI300")
    assert effects.contribution_multipliers["EQ03_CN_DISCOUNT_RATE"] <= .55
    assert "falling_yields_growth_scare" in effects.risk_flags


def test_idiosyncratic_rmb_weakness_reaches_equity_risk_flags():
    from widegold.schemas.factors import FactorState, FactorStateComponents
    from widegold.domain.enums import DataStatus
    from datetime import datetime, timezone

    state = FactorState(
        factor_id="EQ09_RMB_EXTERNAL_BALANCE", as_of_ts=datetime.now(timezone.utc), state=-60,
        components=FactorStateComponents(level=-60), reliability=.5, coverage=1,
        status=DataStatus.VALID, quality_flags=["idiosyncratic_rmb_weakness"],
    )
    effects = evaluate_conflicts([state], "CSI300")
    assert "idiosyncratic_rmb_weakness" in effects.risk_flags
