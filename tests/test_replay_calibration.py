from datetime import date, datetime, timedelta, timezone

from widegold.data.providers.replay import StoredPointInTimeProvider
from widegold.repositories.memory import reset_memory_repository, snapshot_repository
from widegold.schemas.events import NewsDocument
from widegold.schemas.indicators import IndicatorObservation
from widegold.schemas.replay import CalibrationRequest, ReplayRequest
from widegold.services.calibration import run_calibration
from widegold.services.mock_analysis import run_mock_analysis
from widegold.services.replay import run_replay
from widegold.settings.config import evaluation_config
from widegold.domain.enums import SourceTier


def _price_observations(asset_id: str, indicator_id: str, rate: float):
    base = date(2026, 9, 10)
    rows = []
    for idx in range(0, 26):
        day = base + timedelta(days=idx)
        rows.append(IndicatorObservation(
            indicator_id=indicator_id,
            observation_date=day,
            release_ts=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=10),
            ingest_ts=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=11),
            value=100.0 * (1.0 + rate * idx),
            source_id="TEST_PRICE",
            revision_vintage="test",
            definition_version="1.0.0",
        ))
    return rows


def test_calibration_uses_published_scores_and_future_trading_observations():
    reset_memory_repository()
    snapshot = run_mock_analysis(publish=True)
    price_map = evaluation_config()["asset_price_indicators"]
    score_map = {item.asset_id: item.score for item in snapshot.assets}
    for asset_id, indicator_id in price_map.items():
        # Monotonic but score-dependent future return makes the cross-asset ordering testable.
        rate = 0.0005 + max(score_map[asset_id] - 50.0, 0.0) / 100000.0
        snapshot_repository.save_indicator_observations(_price_observations(asset_id, indicator_id, rate))

    result = run_calibration(
        CalibrationRequest(start_date=date(2026, 9, 10), end_date=date(2026, 9, 10), horizons=[1, 5, 20]),
        repo=snapshot_repository,
        evaluation_as_of=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    assert result.published_runs == 1
    assert result.evaluated_samples == 21
    assert len(result.asset_metrics) == 21
    assert result.rank_ic_by_horizon[5] is not None
    assert snapshot_repository.get_calibration_report(result.calibration_id) is not None


def test_stored_replay_never_uses_documents_retrieved_after_cutoff():
    reset_memory_repository()
    cutoff = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    late_doc = NewsDocument(
        source_id="TEST",
        source_tier=SourceTier.C,
        title="黄金新闻",
        content="test",
        published_at=cutoff - timedelta(hours=1),
        retrieved_at=cutoff + timedelta(hours=1),
    )
    snapshot_repository.save_documents([late_doc])
    provider = StoredPointInTimeProvider(as_of=cutoff, repo=snapshot_repository)
    bundle = provider.load()
    assert bundle.news == []
    assert "replay_news_history_unavailable" in bundle.news_warnings


def test_replay_with_missing_history_degrades_instead_of_crashing():
    reset_memory_repository()
    result = run_replay(
        ReplayRequest(start_date=date(2026, 9, 10), end_date=date(2026, 9, 10)),
        repo=snapshot_repository,
    )
    assert result.failed == 0
    assert result.degraded == 1
    assert len(result.days) == 1
    assert result.days[0].status == "QUALITY_FAILED"
