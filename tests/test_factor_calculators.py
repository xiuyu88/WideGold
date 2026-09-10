from datetime import date, datetime, timedelta, timezone

from widegold.domain.enums import DataStatus
from widegold.engine.factor_calculators import calculate_all_factor_inputs
from widegold.schemas.indicators import IndicatorObservation


AS_OF = datetime(2026, 9, 10, 18, 10, tzinfo=timezone.utc)


def series(indicator_id: str, values: list[float], *, asset_id=None, spacing_days=1):
    # Synthetic history should end at AS_OF so freshness logic is exercised without
    # accidentally turning every test series into an expired provider feed.
    start = AS_OF.date() - timedelta(days=(len(values) - 1) * spacing_days)
    return [
        IndicatorObservation(
            indicator_id=indicator_id,
            asset_id=asset_id,
            observation_date=start + timedelta(days=i * spacing_days),
            release_ts=AS_OF - timedelta(days=max(0, len(values) - i - 1)),
            ingest_ts=AS_OF,
            value=value,
            source_id="TEST",
        )
        for i, value in enumerate(values)
    ]


def test_asset_scoped_trend_factor_is_different_by_index():
    histories = {
        ("CSI300_CLOSE", None): series("CSI300_CLOSE", [100 + i * 0.2 for i in range(90)]),
        ("CSI_A500_CLOSE", None): series("CSI_A500_CLOSE", [100 + i * 0.1 for i in range(90)]),
        ("CSI500_CLOSE", None): series("CSI500_CLOSE", [100 + i * 0.15 for i in range(90)]),
        ("CSI1000_CLOSE", None): series("CSI1000_CLOSE", [120 - i * 0.15 for i in range(90)]),
        ("CHINEXT_CLOSE", None): series("CHINEXT_CLOSE", [100 + i * 0.25 for i in range(90)]),
        ("STAR50_CLOSE", None): series("STAR50_CLOSE", [100 + i * 0.3 for i in range(90)]),
    }
    rows = calculate_all_factor_inputs(histories, AS_OF)
    trend = {(row.factor_id, row.asset_id): row for row in rows if row.factor_id == "EQ14_TREND_MOMENTUM"}
    assert trend[("EQ14_TREND_MOMENTUM", "CSI300")].value > 0
    assert trend[("EQ14_TREND_MOMENTUM", "CSI1000")].value < 0


def test_partial_composite_factor_survives_missing_indicator():
    histories = {
        ("US_REAL_YIELD_10Y", None): series("US_REAL_YIELD_10Y", [2.2 - i * 0.01 for i in range(80)]),
        ("US_BROAD_DOLLAR", None): series("US_BROAD_DOLLAR", [108 - i * 0.03 for i in range(80)]),
        # US_VIX deliberately missing
    }
    rows = calculate_all_factor_inputs(histories, AS_OF)
    factor = next(row for row in rows if row.factor_id == "EQ10_GLOBAL_FIN_CONDITIONS")
    assert factor.status == DataStatus.PARTIAL
    assert factor.value is not None
    assert factor.reliability > 0
    assert any("US_VIX" in warning for warning in factor.warnings)


def test_gold_inflation_requires_macro_confirmation():
    rising_cpi = [2.0 + i * 0.08 for i in range(18)]
    # Gold-friendly confirmation: real yields and dollar both trend lower.
    friendly = {
        ("US_CPI_YOY", None): series("US_CPI_YOY", rising_cpi, spacing_days=30),
        ("US_REAL_YIELD_10Y", None): series("US_REAL_YIELD_10Y", [2.8 - i * 0.03 for i in range(80)]),
        ("US_BROAD_DOLLAR", None): series("US_BROAD_DOLLAR", [112 - i * 0.08 for i in range(80)]),
    }
    hostile = {
        ("US_CPI_YOY", None): series("US_CPI_YOY", rising_cpi, spacing_days=30),
        ("US_REAL_YIELD_10Y", None): series("US_REAL_YIELD_10Y", [1.0 + i * 0.03 for i in range(80)]),
        ("US_BROAD_DOLLAR", None): series("US_BROAD_DOLLAR", [95 + i * 0.08 for i in range(80)]),
    }
    friendly_row = next(r for r in calculate_all_factor_inputs(friendly, AS_OF) if r.factor_id == "G05_INFLATION_REGIME")
    hostile_row = next(r for r in calculate_all_factor_inputs(hostile, AS_OF) if r.factor_id == "G05_INFLATION_REGIME")
    assert friendly_row.value is not None and friendly_row.value > 0
    assert hostile_row.value is not None and hostile_row.value < friendly_row.value
    assert hostile_row.value <= 0


def test_gold_inflation_is_neutral_when_inflation_is_not_rising():
    histories = {
        ("US_CPI_YOY", None): series("US_CPI_YOY", [3.0 for _ in range(18)], spacing_days=30),
        ("US_REAL_YIELD_10Y", None): series("US_REAL_YIELD_10Y", [2.5 - i * 0.03 for i in range(80)]),
        ("US_BROAD_DOLLAR", None): series("US_BROAD_DOLLAR", [110 - i * 0.08 for i in range(80)]),
    }
    row = next(r for r in calculate_all_factor_inputs(histories, AS_OF) if r.factor_id == "G05_INFLATION_REGIME")
    assert row.value == 0.0


def test_expired_daily_indicator_is_not_refreshed_by_today_ingest_timestamp():
    old = AS_OF - timedelta(days=400)
    rows = []
    for i in range(80):
        ts = old + timedelta(days=i)
        rows.append(IndicatorObservation(
            indicator_id="US_REAL_YIELD_10Y",
            observation_date=ts.date(),
            release_ts=AS_OF,  # live fetch first-known timestamp is today
            ingest_ts=AS_OF,
            value=2.5 - i * 0.01,
            source_id="TEST",
        ))
    result = calculate_all_factor_inputs({("US_REAL_YIELD_10Y", None): rows}, AS_OF)
    g01 = next(r for r in result if r.factor_id == "G01_US_REAL_YIELD")
    assert g01.status == DataStatus.UNAVAILABLE
    assert g01.value is None
    assert any("hard_expired" in warning for warning in g01.warnings)


def test_equity_erp_uses_pe_and_cgb_yield_with_asset_scope():
    # Create recent PE/rate histories ending at AS_OF. CSI300 becomes cheaper while CSI1000
    # becomes more expensive; both use the same government-bond curve.
    rates = series("CN_CGB_10Y", [2.4 - i * 0.003 for i in range(90)])
    csi300_pe = series("CN_INDEX_PE", [15.0 - i * 0.03 for i in range(90)], asset_id="CSI300")
    csi1000_pe = series("CN_INDEX_PE", [20.0 + i * 0.04 for i in range(90)], asset_id="CSI1000")
    rows = calculate_all_factor_inputs({
        ("CN_CGB_10Y", None): rates,
        ("CN_INDEX_PE", "CSI300"): csi300_pe,
        ("CN_INDEX_PE", "CSI1000"): csi1000_pe,
    }, AS_OF)
    values = {(r.factor_id, r.asset_id): r for r in rows if r.factor_id == "EQ07_VALUATION_ERP"}
    assert values[("EQ07_VALUATION_ERP", "CSI300")].value is not None
    assert values[("EQ07_VALUATION_ERP", "CSI300")].value > 0
    assert values[("EQ07_VALUATION_ERP", "CSI1000")].value is not None
    assert values[("EQ07_VALUATION_ERP", "CSI1000")].value < values[("EQ07_VALUATION_ERP", "CSI300")].value
    assert any(w.startswith("erp_pct_points:") for w in values[("EQ07_VALUATION_ERP", "CSI300")].warnings)


def test_funding_liquidity_combines_level_and_short_medium_changes():
    falling = {("CN_DR007", None): series("CN_DR007", [2.5 - i * 0.012 for i in range(90)])}
    rising = {("CN_DR007", None): series("CN_DR007", [1.2 + i * 0.012 for i in range(90)])}
    positive = next(r for r in calculate_all_factor_inputs(falling, AS_OF) if r.factor_id == "EQ01_CN_FUNDING_LIQUIDITY")
    negative = next(r for r in calculate_all_factor_inputs(rising, AS_OF) if r.factor_id == "EQ01_CN_FUNDING_LIQUIDITY")
    assert positive.value is not None and positive.value > 0
    assert negative.value is not None and negative.value < 0
    assert any(w.startswith("funding_components:") for w in positive.warnings)


def test_price_profit_cycle_caps_reflation_without_profit_confirmation():
    histories = {
        ("CN_PPI_YOY", None): series("CN_PPI_YOY", [-4 + i * 0.45 for i in range(20)], spacing_days=30),
        ("CN_INDUSTRIAL_PROFIT_YOY", None): series(
            "CN_INDUSTRIAL_PROFIT_YOY", [12 - i * 1.5 for i in range(20)], spacing_days=30
        ),
    }
    row = next(r for r in calculate_all_factor_inputs(histories, AS_OF) if r.factor_id == "EQ05_PRICE_PROFIT_CYCLE")
    assert row.value is not None
    assert row.value <= 10.0
    assert "ppi_reflation_without_profit_confirmation" in row.warnings


def test_price_profit_cycle_can_degrade_to_muted_ppi_only_proxy():
    histories = {
        ("CN_PPI_YOY", None): series("CN_PPI_YOY", [-3 + i * 0.25 for i in range(20)], spacing_days=30),
    }
    row = next(r for r in calculate_all_factor_inputs(histories, AS_OF) if r.factor_id == "EQ05_PRICE_PROFIT_CYCLE")
    assert row.status == DataStatus.PARTIAL
    assert row.value is not None and abs(row.value) <= 35
    assert "profit_data_missing_ppi_only_proxy" in row.warnings


def test_cftc_extreme_long_crowding_reduces_positive_confirmation():
    # Build a history with a large late acceleration, making both level and change strongly positive.
    crowded_values = [80 + i * 0.5 for i in range(40)] + [100 + i * 6 for i in range(8)]
    moderate_values = [80 + i * 0.5 for i in range(48)]
    crowded = next(
        r for r in calculate_all_factor_inputs({("CFTC_GOLD_NET_LONG", None): series("CFTC_GOLD_NET_LONG", crowded_values, spacing_days=7)}, AS_OF)
        if r.factor_id == "G08_CFTC_POSITIONING"
    )
    moderate = next(
        r for r in calculate_all_factor_inputs({("CFTC_GOLD_NET_LONG", None): series("CFTC_GOLD_NET_LONG", moderate_values, spacing_days=7)}, AS_OF)
        if r.factor_id == "G08_CFTC_POSITIONING"
    )
    assert crowded.value is not None and moderate.value is not None
    assert any(w.startswith("cftc_long_crowding:") for w in crowded.warnings)
    assert crowded.value < 85



def test_credit_money_single_channel_is_partial_and_muted():
    histories = {
        ("CN_M1_YOY", None): series("CN_M1_YOY", [2 + i * 0.3 for i in range(20)], spacing_days=30),
    }
    row = next(r for r in calculate_all_factor_inputs(histories, AS_OF) if r.factor_id == "EQ02_CN_CREDIT_MONEY")
    assert row.status == DataStatus.PARTIAL
    assert row.value is not None and abs(row.value) <= 35
    assert "credit_money_single_channel_partial" in row.warnings


def test_credit_money_divergence_is_flagged_and_capped():
    histories = {
        ("CN_M1_YOY", None): series("CN_M1_YOY", [1 + i * 0.5 for i in range(24)], spacing_days=30),
        ("CN_TSFIN_GROWTH", None): series("CN_TSFIN_GROWTH", [3000 + i * 30 for i in range(12)] + [2200 - i * 50 for i in range(12)], spacing_days=30),
    }
    row = next(r for r in calculate_all_factor_inputs(histories, AS_OF) if r.factor_id == "EQ02_CN_CREDIT_MONEY")
    assert row.value is not None
    assert any("credit" in w and ("divergence" in w or "confirmation" in w or "activation" in w) for w in row.warnings)
    assert row.value <= 35


def test_growth_momentum_single_pmi_release_is_partial_not_full_strength():
    histories = {
        ("CN_PMI_MANUFACTURING", None): series("CN_PMI_MANUFACTURING", [48.0 + i * 0.25 for i in range(16)], spacing_days=30),
    }
    row = next(r for r in calculate_all_factor_inputs(histories, AS_OF) if r.factor_id == "EQ04_CN_GROWTH_MOMENTUM")
    assert row.status == DataStatus.PARTIAL
    assert row.value is not None and abs(row.value) <= 45
    assert "growth_signal_partial" in row.warnings


def test_central_bank_gold_china_only_is_bounded_partial_proxy():
    histories = {
        ("CN_OFFICIAL_GOLD_RESERVE", None): series("CN_OFFICIAL_GOLD_RESERVE", [1900 + i * 20 for i in range(18)], spacing_days=30),
    }
    row = next(r for r in calculate_all_factor_inputs(histories, AS_OF) if r.factor_id == "G06_CENTRAL_BANK_DEMAND")
    assert row.status == DataStatus.PARTIAL
    assert row.value is not None and abs(row.value) <= 25
    assert "global_central_bank_demand_missing_china_proxy_only" in row.warnings



def test_rmb_external_balance_controls_for_broad_dollar_move():
    # USD/CNY and broad USD rise together: most pressure is global-dollar, so EQ09 should be much
    # less negative than an idiosyncratic RMB selloff of similar USD/CNY magnitude.
    common = {
        ("USDCNY", None): series("USDCNY", [6.8 + i * 0.006 for i in range(80)]),
        ("US_BROAD_DOLLAR", None): series("US_BROAD_DOLLAR", [100 + i * 0.09 for i in range(80)]),
    }
    idiosyncratic = {
        ("USDCNY", None): series("USDCNY", [6.8 + i * 0.006 for i in range(80)]),
        ("US_BROAD_DOLLAR", None): series("US_BROAD_DOLLAR", [105 for _ in range(80)]),
    }
    common_row = next(r for r in calculate_all_factor_inputs(common, AS_OF) if r.factor_id == "EQ09_RMB_EXTERNAL_BALANCE")
    idio_row = next(r for r in calculate_all_factor_inputs(idiosyncratic, AS_OF) if r.factor_id == "EQ09_RMB_EXTERNAL_BALANCE")
    assert common_row.value is not None and idio_row.value is not None
    assert idio_row.value < common_row.value


def test_rmb_external_balance_without_dollar_control_is_partial_and_muted():
    histories = {("USDCNY", None): series("USDCNY", [6.8 + i * 0.006 for i in range(80)])}
    row = next(r for r in calculate_all_factor_inputs(histories, AS_OF) if r.factor_id == "EQ09_RMB_EXTERNAL_BALANCE")
    assert row.status == DataStatus.PARTIAL
    assert row.value is not None and abs(row.value) <= 30
    assert "rmb_direction_only_partial_proxy" in row.warnings
