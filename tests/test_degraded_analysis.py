from widegold.data.providers.mock import MockAnalysisDataProvider
from widegold.domain.enums import PublishMode
from widegold.repositories.memory import reset_memory_repository
from widegold.schemas.common import AnalysisRunRequest
from widegold.services.analysis import run_analysis


def test_partial_factor_failure_still_returns_preview():
    reset_memory_repository()
    failures = {
        "EQ02_CN_CREDIT_MONEY",
        "EQ05_PRICE_PROFIT_CYCLE",
        "EQ09_RMB_EXTERNAL_BALANCE",
        "EQ11_FOREIGN_ACTIVITY",
        "G05_INFLATION_REGIME",
        "G08_CFTC_POSITIONING",
        "G10_SUPPLY_FABRICATION",
    }
    snapshot = run_analysis(
        AnalysisRunRequest(publish_mode=PublishMode.PREVIEW_ONLY),
        provider=MockAnalysisDataProvider(fail_factors=failures),
    )
    assert len(snapshot.assets) == 7
    assert snapshot.status in {"PREVIEW_READY", "QUALITY_FAILED"}
    assert snapshot.factor_resolution["unavailable"] >= 1
    # Service returns a complete analysis object even if publication quality is degraded.
    assert all(0 <= asset.score <= 100 for asset in snapshot.assets)


def test_auto_publish_is_blocked_when_too_many_factors_fail():
    reset_memory_repository()
    failures = {
        "EQ01_CN_FUNDING_LIQUIDITY",
        "EQ02_CN_CREDIT_MONEY",
        "EQ03_CN_DISCOUNT_RATE",
        "EQ04_CN_GROWTH_MOMENTUM",
        "EQ05_PRICE_PROFIT_CYCLE",
        "EQ06_INDEX_EARNINGS",
        "EQ07_VALUATION_ERP",
        "EQ11_FOREIGN_ACTIVITY",
        "EQ12_DOMESTIC_FLOW_LEVERAGE",
        "EQ13_MARKET_BREADTH_LIQUIDITY",
        "G01_US_REAL_YIELD",
        "G02_USD",
        "G04_RISK_UNCERTAINTY",
        "G07_GOLD_ETF_FLOW",
        "G11_USDCNY_TRANSLATION",
    }
    snapshot = run_analysis(
        AnalysisRunRequest(publish_mode=PublishMode.AUTO),
        provider=MockAnalysisDataProvider(fail_factors=failures),
    )
    assert snapshot.published is False
    assert snapshot.status in {"QUALITY_FAILED", "PREVIEW_READY"}
    assert any("AUTO_PUBLISH_BLOCKED" in item for item in snapshot.warnings)



def test_disabled_provider_marks_news_source_unavailable():
    from widegold.data.providers.disabled import DisabledAnalysisDataProvider
    from widegold.domain.enums import DataStatus

    bundle = DisabledAnalysisDataProvider().load()
    assert bundle.news == []
    assert bundle.news_status == DataStatus.UNAVAILABLE
    assert bundle.news_warnings


def test_all_raw_indicators_unavailable_keeps_previous_published_snapshot():
    from datetime import datetime, timezone

    from widegold.data.providers.raw_indicators import RawIndicatorAnalysisProvider
    from widegold.engine.factor_calculators import required_indicator_keys
    from widegold.repositories.memory import snapshot_repository
    from widegold.services.indicator_collection import IndicatorCollectionResult

    reset_memory_repository()
    baseline = run_analysis(
        AnalysisRunRequest(publish_mode=PublishMode.EXPLICIT_PUBLISH),
        provider=MockAnalysisDataProvider(),
    )
    assert snapshot_repository.latest_published().analysis_run_id == baseline.analysis_run_id

    class AllUnavailableCollector:
        repo = snapshot_repository

        def collect(self, keys, as_of, *, force_refresh=False):
            assert keys == required_indicator_keys()
            return IndicatorCollectionResult(
                histories={key: [] for key in keys},
                unavailable=len(keys),
                warnings=["synthetic_all_indicators_unavailable"],
            )

    degraded = run_analysis(
        AnalysisRunRequest(publish_mode=PublishMode.AUTO),
        provider=RawIndicatorAnalysisProvider(
            as_of=datetime(2026, 9, 11, 10, tzinfo=timezone.utc),
            collector=AllUnavailableCollector(),
        ),
    )

    assert degraded.published is False
    assert degraded.status in {"QUALITY_FAILED", "PREVIEW_READY"}
    assert degraded.factor_resolution["stale"] == degraded.factor_resolution["state_rows"]
    assert all(
        asset["fresh_weighted_coverage"] == 0.0
        for asset in degraded.quality_gate["assets"]
    )
    assert snapshot_repository.latest_published().analysis_run_id == baseline.analysis_run_id
