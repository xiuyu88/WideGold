from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pandas as pd

from widegold.data.indicators import dataframe_to_observations, parse_date, parse_numeric
from widegold.data.providers.akshare_derived import AkshareDerivedIndicatorProvider
from widegold.data.providers.akshare_provider import AkshareIndicatorProvider
from widegold.engine.factor_calculators import calculate_all_factor_inputs
from widegold.schemas.indicators import IndicatorFetchRequest, IndicatorObservation


def _obs(indicator: str, values: list[float], *, asset_id=None, start=None):
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    result = []
    for i, value in enumerate(values):
        ts = start + timedelta(days=30 * i)
        result.append(IndicatorObservation(
            indicator_id=indicator,
            asset_id=asset_id,
            observation_date=ts.date(),
            release_ts=ts,
            ingest_ts=ts,
            value=value,
            source_id="TEST",
        ))
    return result


def test_parse_common_china_macro_values_and_dates():
    assert parse_numeric("10.40%") == 10.4
    assert parse_numeric("1,234.5") == 1234.5
    assert parse_date("2026年08月份").isoformat() == "2026-08-01"
    assert parse_date("2026.8").isoformat() == "2026-08-01"
    assert parse_date("202608").isoformat() == "2026-08-01"


def test_dataframe_parser_accepts_percent_strings():
    frame = pd.DataFrame({"统计时间": ["2026.7", "2026.8"], "M1": ["4.60%", "5.10%"]})
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    rows = dataframe_to_observations(
        indicator_id="CN_M1_YOY",
        source_id="TEST",
        frame=frame,
        date_column="统计时间",
        value_column="M1",
        release_ts=now,
    )
    assert [r.value for r in rows] == [4.6, 5.1]


def test_akshare_provider_uses_asset_specific_symbol(monkeypatch):
    calls = []

    def fake_hist(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame({"日期": ["2026-09-09"], "滚动市盈率": [12.5]})

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_zh_index_hist_csindex=fake_hist))
    req = IndicatorFetchRequest(
        indicator_id="CN_INDEX_ERP",
        asset_id="CSI300",
        start_date=datetime(2026, 9, 1).date(),
        end_date=datetime(2026, 9, 10).date(),
        as_of=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    spec = {
        "source_id": "CSINDEX",
        "params": {
            "function": "stock_zh_index_hist_csindex",
            "kwargs": {"start_date": "$START_YYYYMMDD", "end_date": "$END_YYYYMMDD"},
            "asset_params": {"CSI300": {"symbol": "000300"}},
        },
        "parser": {"date_column": "日期", "value_column": "滚动市盈率"},
    }
    out = AkshareIndicatorProvider().fetch(req, spec)
    assert out.observations[0].asset_id == "CSI300"
    assert calls[0]["symbol"] == "000300"


def test_derived_margin_total_aggregates_sh_sz(monkeypatch):
    sh = pd.DataFrame({"日期": ["2026-09-09"], "融资余额": [100.0]})
    sz = pd.DataFrame({"日期": ["2026-09-09"], "融资余额": [250.0]})
    fake = SimpleNamespace(
        macro_china_market_margin_sh=lambda: sh,
        macro_china_market_margin_sz=lambda: sz,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)
    req = IndicatorFetchRequest(
        indicator_id="CN_MARGIN_BALANCE",
        start_date=datetime(2026, 9, 1).date(),
        end_date=datetime(2026, 9, 10).date(),
        as_of=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    out = AkshareDerivedIndicatorProvider().fetch(req, {
        "source_id": "TEST",
        "params": {"derived": "cn_margin_total"},
    })
    assert out.observations[0].value == 350.0


def test_gold_china_premium_is_reconstructed_from_raw_series():
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    # Create 25 days so the SGE trend component is also available. The latest theoretical
    # RMB/gram is 3000 * 7 / 31.1034768 ~= 675.17; SGE 700 therefore carries a positive premium.
    sge = _obs("SGE_AU9999", [650 + i * 2 for i in range(25)], start=now - timedelta(days=24*30))
    gold = _obs("GOLD_USD", [2900 + i * 4 for i in range(25)], start=now - timedelta(days=24*30))
    fx = _obs("USDCNY", [7.0 for _ in range(25)], start=now - timedelta(days=24*30))
    # Force latest points to a clearly positive local premium.
    sge[-1] = sge[-1].model_copy(update={"value": 700.0, "release_ts": now, "observation_date": now.date()})
    gold[-1] = gold[-1].model_copy(update={"value": 3000.0, "release_ts": now, "observation_date": now.date()})
    fx[-1] = fx[-1].model_copy(update={"value": 7.0, "release_ts": now, "observation_date": now.date()})
    histories = {
        ("SGE_AU9999", None): sge,
        ("GOLD_USD", None): gold,
        ("USDCNY", None): fx,
    }
    inputs = calculate_all_factor_inputs(histories, now)
    g09 = next(x for x in inputs if x.factor_id == "G09_CHINA_PHYSICAL_PREMIUM")
    assert g09.value is not None and g09.value > 0
    assert any(w.startswith("sge_premium_pct:") for w in g09.warnings)


def test_akshare_provider_can_semantically_resolve_cftc_gold_column(monkeypatch):
    frame = pd.DataFrame({
        "日期": ["2026-08-25", "2026-09-01"],
        "黄金-多头仓位": [200000, 205000],
        "黄金-空头仓位": [100000, 102000],
        "黄金-净仓位": [100000, 103000],
        "白银-净仓位": [50000, 51000],
    })
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(macro_usa_cftc_c_holding=lambda: frame))
    req = IndicatorFetchRequest(
        indicator_id="CFTC_GOLD_NET_LONG",
        start_date=datetime(2026, 8, 1).date(),
        end_date=datetime(2026, 9, 10).date(),
        as_of=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    out = AkshareIndicatorProvider().fetch(req, {
        "source_id": "TEST",
        "params": {"function": "macro_usa_cftc_c_holding", "kwargs": {}},
        "parser": {"date_column": "日期", "value_column_contains": ["黄金", "净仓位"]},
    })
    assert [row.value for row in out.observations] == [100000.0, 103000.0]


def test_akshare_provider_reads_spdr_gold_holdings(monkeypatch):
    frame = pd.DataFrame({
        "商品": ["黄金", "黄金"],
        "日期": ["2026-09-01", "2026-09-02"],
        "总库存": [980.1, 982.4],
        "增持/减持": [0.0, 2.3],
    })
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(macro_cons_gold=lambda: frame))
    req = IndicatorFetchRequest(
        indicator_id="GOLD_ETF_HOLDINGS",
        start_date=datetime(2026, 8, 1).date(),
        end_date=datetime(2026, 9, 10).date(),
        as_of=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    out = AkshareIndicatorProvider().fetch(req, {
        "source_id": "TEST",
        "params": {"function": "macro_cons_gold", "kwargs": {}},
        "parser": {"date_column": "日期", "value_column": "总库存"},
    })
    assert len(out.observations) == 2
    assert out.observations[-1].value == 982.4


def test_akshare_news_provider_filters_by_relevance_and_time(monkeypatch):
    from widegold.data.providers.news_akshare import AkshareGlobalNewsProvider

    frame = pd.DataFrame({
        "标题": ["美联储释放降息信号", "某公司发布手机新品", "黄金价格受到实际利率影响"],
        "摘要": ["鲍威尔谈到通胀和利率", "新品发布", "美元和实际利率出现变化"],
        "发布时间": ["2026-09-10 17:00:00", "2026-09-10 16:00:00", "2026-09-08 10:00:00"],
        "链接": ["https://a", "https://b", "https://c"],
    })
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_info_global_em=lambda: frame))
    as_of = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)  # 18:00 China time
    docs = AkshareGlobalNewsProvider().fetch(
        as_of=as_of,
        lookback_hours=30,
        max_documents=20,
        keywords=["美联储", "黄金", "实际利率"],
    )
    assert len(docs) == 1
    assert docs[0].title == "美联储释放降息信号"
    assert docs[0].source_tier.value == "C"


def test_version_snapshot_includes_news_collection():
    from widegold.settings.config import current_version_snapshot, news_config
    snapshot = current_version_snapshot()
    assert snapshot.news_collection == str(news_config().get("version", "1.0.0"))


def test_indicator_registry_uses_real_spdr_proxy_and_growth_inputs():
    from widegold.settings.config import factor_calculator_config, indicator_config
    indicators = {row["id"]: row for row in indicator_config()["indicators"]}
    assert indicators["GOLD_ETF_HOLDINGS"]["provider"] == "akshare"
    assert indicators["GOLD_ETF_HOLDINGS"]["params"]["function"] == "macro_cons_gold"
    assert indicators["CN_INDUSTRIAL_PRODUCTION_YOY"]["params"]["function"] == "macro_china_industrial_production_yoy"
    assert indicators["CN_EXPORTS_YOY"]["params"]["function"] == "macro_china_exports_yoy"
    growth_inputs = factor_calculator_config()["factors"]["EQ04_CN_GROWTH_MOMENTUM"]["inputs"]
    assert {row["indicator"] for row in growth_inputs} >= {
        "CN_PMI_MANUFACTURING", "CN_INDUSTRIAL_PRODUCTION_YOY", "CN_EXPORTS_YOY"
    }


def test_china_official_gold_reserve_is_partial_proxy_for_g06():
    from widegold.settings.config import factor_calculator_config
    inputs = factor_calculator_config()["factors"]["G06_CENTRAL_BANK_DEMAND"]["inputs"]
    assert any(row["indicator"] == "CN_OFFICIAL_GOLD_RESERVE" for row in inputs)
    assert any(row["indicator"] == "GLOBAL_CENTRAL_BANK_GOLD" for row in inputs)


def test_akshare_provider_parses_industrial_production_and_exports(monkeypatch):
    growth = pd.DataFrame({"商品": ["中国规模以上工业增加值年率报告"], "日期": ["2026-08-17"], "今值": [5.3], "预测值": [5.1], "前值": [5.2]})
    exports = pd.DataFrame({"商品": ["中国以美元计算出口年率报告"], "日期": ["2026-08-07"], "今值": [7.2], "预测值": [5.5], "前值": [5.8]})
    fake = SimpleNamespace(
        macro_china_industrial_production_yoy=lambda: growth,
        macro_china_exports_yoy=lambda: exports,
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)
    as_of = datetime(2026, 9, 10, tzinfo=timezone.utc)
    provider = AkshareIndicatorProvider()
    for indicator_id, fn_name, expected in (
        ("CN_INDUSTRIAL_PRODUCTION_YOY", "macro_china_industrial_production_yoy", 5.3),
        ("CN_EXPORTS_YOY", "macro_china_exports_yoy", 7.2),
    ):
        req = IndicatorFetchRequest(
            indicator_id=indicator_id,
            start_date=datetime(2026, 1, 1).date(),
            end_date=as_of.date(),
            as_of=as_of,
        )
        out = provider.fetch(req, {
            "source_id": "TEST",
            "params": {"function": fn_name, "kwargs": {}},
            "parser": {"date_column": "日期", "value_column": "今值"},
        })
        assert out.observations[-1].value == expected


def test_akshare_provider_parses_china_official_gold_reserve(monkeypatch):
    frame = pd.DataFrame({"统计时间": ["2026.7", "2026.8"], "黄金储备": [7390.0, 7410.0], "国家外汇储备": [32000.0, 32100.0]})
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(macro_china_foreign_exchange_gold=lambda: frame))
    req = IndicatorFetchRequest(
        indicator_id="CN_OFFICIAL_GOLD_RESERVE",
        start_date=datetime(2026, 1, 1).date(),
        end_date=datetime(2026, 9, 10).date(),
        as_of=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    out = AkshareIndicatorProvider().fetch(req, {
        "source_id": "TEST",
        "params": {"function": "macro_china_foreign_exchange_gold", "kwargs": {}},
        "parser": {"date_column": "统计时间", "value_column": "黄金储备"},
    })
    assert [x.value for x in out.observations] == [7390.0, 7410.0]
