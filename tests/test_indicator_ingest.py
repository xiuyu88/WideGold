from datetime import date, datetime, timezone

import pytest

from widegold.repositories.memory import reset_memory_repository, snapshot_repository
from widegold.schemas.indicators import IndicatorObservation
from widegold.services.indicator_ingest import ingest_indicator_observations


def setup_function():
    reset_memory_repository()


def test_indicator_ingest_and_point_in_time_read():
    known = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    item = IndicatorObservation(
        indicator_id="US_REAL_YIELD_10Y",
        observation_date=date(2026, 9, 9),
        release_ts=known,
        ingest_ts=known,
        value=1.75,
        source_id="TEST",
    )
    result = ingest_indicator_observations([item])
    assert result["accepted"] == 1
    assert snapshot_repository.get_indicator_history(
        "US_REAL_YIELD_10Y", as_of=known - __import__('datetime').timedelta(seconds=1)
    ) == []
    rows = snapshot_repository.get_indicator_history("US_REAL_YIELD_10Y", as_of=known)
    assert rows[0].value == 1.75


def test_unknown_indicator_is_rejected():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        ingest_indicator_observations([
            IndicatorObservation(
                indicator_id="UNKNOWN_X",
                observation_date=now.date(), release_ts=now, ingest_ts=now,
                value=1.0, source_id="TEST",
            )
        ])


def test_external_indicator_rejects_out_of_range_value():
    now = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="outside expected range"):
        ingest_indicator_observations([IndicatorObservation(
            indicator_id="CN_DR007", observation_date=now.date(), release_ts=now, ingest_ts=now,
            value=145.0, source_id="BAD_UNIT",
        )])


def test_asset_scoped_external_indicator_requires_asset_id():
    now = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="asset_id is required"):
        ingest_indicator_observations([IndicatorObservation(
            indicator_id="CN_INDEX_EARNINGS_REV", observation_date=now.date(), release_ts=now, ingest_ts=now,
            value=12.0, source_id="TEST",
        )])


def test_industrial_profit_indicator_requires_aggregation_scope():
    now = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="aggregation_scope"):
        ingest_indicator_observations([IndicatorObservation(
            indicator_id="CN_INDUSTRIAL_PROFIT_YOY", observation_date=now.date(), release_ts=now, ingest_ts=now,
            value=8.0, source_id="TEST",
        )])


def test_dr007_rejects_fr007_semantics_even_when_value_range_is_valid():
    now = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="metadata.benchmark must equal 'DR007'"):
        ingest_indicator_observations([IndicatorObservation(
            indicator_id="CN_DR007", observation_date=now.date(), release_ts=now, ingest_ts=now,
            value=1.5, source_id="BAD_PROXY", metadata={"benchmark": "FR007"},
        )])


def test_earnings_revision_requires_methodology_and_positive_window():
    now = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="window_days"):
        ingest_indicator_observations([IndicatorObservation(
            indicator_id="CN_INDEX_EARNINGS_REV", asset_id="CSI300",
            observation_date=now.date(), release_ts=now, ingest_ts=now,
            value=12.0, source_id="VENDOR",
            metadata={"methodology": "revision_breadth", "window_days": 0},
        )])


def test_foreign_activity_rejects_turnover_only_proxy():
    now = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="measure_type"):
        ingest_indicator_observations([IndicatorObservation(
            indicator_id="CN_FOREIGN_ACTIVITY", observation_date=now.date(),
            release_ts=now, ingest_ts=now, value=12.0, source_id="CONNECT",
            metadata={"directionality": "signed", "measure_type": "turnover_only"},
        )])


def test_gold_supply_demand_requires_versioned_structural_methodology():
    now = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="metadata.methodology"):
        ingest_indicator_observations([IndicatorObservation(
            indicator_id="GOLD_SUPPLY_DEMAND", observation_date=now.date(),
            release_ts=now, ingest_ts=now, value=20.0, source_id="WGC",
            metadata={"methodology": "raw_tonnes", "components": ["mine_supply", "jewellery"]},
        )])
