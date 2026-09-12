"""Regression tests for the V1.1 code-review fixes.

Each test pins one defect that existed in V1 and was invisible to the previous suite: a penalty
that was computed but never applied, a sign filter that silently dropped factors, a dashboard
ordering that a manual publish could rewind, and a timeout that never fired.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from widegold.engine.confidence import calculate_confidence
from widegold.engine.rules import evaluate_conflicts
from widegold.repositories.memory import reset_memory_repository, snapshot_repository
from widegold.resilience.executor import ResilientExecutor
from widegold.schemas.factors import FactorState, FactorStateComponents
from widegold.schemas.scores import DashboardSnapshot, FactorContribution

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)


def _state(factor_id: str, value: float, *, quality_flags: list[str] | None = None) -> FactorState:
    return FactorState(
        factor_id=factor_id,
        as_of_ts=NOW,
        state=value,
        components=FactorStateComponents(level=value),
        reliability=0.7,
        coverage=1.0,
        quality_flags=quality_flags or [],
    )


def _contribution(factor_id: str, group: str, sensitivity: float) -> FactorContribution:
    return FactorContribution(
        factor_id=factor_id,
        group=group,
        factor_state=40.0,
        sensitivity=sensitivity,
        reliability=0.7,
        raw_contribution=10.0,
        after_conflict=10.0,
        after_group_cap=10.0,
    )


def test_gold_conflict_penalty_actually_reaches_confidence():
    states = [_state("G08_CFTC_POSITIONING", 55, quality_flags=["cftc_long_crowding:0.800"])]
    effects = evaluate_conflicts(states, asset_id="RMB_GOLD")
    assert effects.confidence_penalty > 0

    result = calculate_confidence(
        "RMB_GOLD",
        [_contribution("G08_CFTC_POSITIONING", "gold_positioning", 2.0)],
        states,
        effects,
    )
    # V1 excluded RMB_GOLD from penalty application while still reporting the penalty in the run
    # trace, so the dashboard showed a deduction the confidence never received.
    assert any(item.startswith("conflict_penalty") for item in result.penalties)


def test_equity_liquidity_conflict_does_not_leak_onto_gold():
    states = [
        _state("EQ01_CN_FUNDING_LIQUIDITY", 55),
        _state("EQ04_CN_GROWTH_MOMENTUM", -55),
        _state("EQ06_INDEX_EARNINGS", -35),
    ]
    equity = evaluate_conflicts(states, asset_id="CSI300")
    gold = evaluate_conflicts(states, asset_id="RMB_GOLD")

    assert "liquidity_without_growth_confirmation" in equity.risk_flags
    assert equity.confidence_penalty == 8.0
    assert gold.risk_flags == []
    assert gold.confidence_penalty == 0.0


def test_confidence_counts_negatively_sensitive_factors():
    states = [_state("G02_USD", 30)]
    effects = evaluate_conflicts(states, asset_id="RMB_GOLD")
    result = calculate_confidence(
        "RMB_GOLD",
        [_contribution("G02_USD", "gold_macro", -4.0)],
        states,
        effects,
    )
    # Filtering on ``sensitivity > 0`` collapsed source quality to zero for this input.
    assert result.source_quality_score > 0


def _snapshot(analysis_date: str, published: bool) -> DashboardSnapshot:
    return DashboardSnapshot(
        analysis_run_id=uuid4(),
        analysis_date=analysis_date,
        as_of=NOW,
        status="PUBLISHED" if published else "PREVIEW_READY",
        published=published,
        assets=[],
        explanations={},
        top_events=[],
    )


def test_dashboard_follows_the_newest_analysis_date_not_publish_time():
    reset_memory_repository()
    current = _snapshot("2026-09-12", published=True)
    backfill = _snapshot("2026-09-01", published=False)
    snapshot_repository.save_snapshot(current, None)
    snapshot_repository.save_snapshot(backfill, None)

    # Publishing an older replay afterwards must not take over the dashboard.
    snapshot_repository.publish(backfill.analysis_run_id)
    latest = snapshot_repository.latest_published()

    assert latest is not None
    assert latest.analysis_date == "2026-09-12"
    assert str(snapshot_repository.latest_published_analysis_date()) == "2026-09-12"


def test_executor_timeout_fires_on_a_blocking_sync_provider():
    executor = ResilientExecutor()
    executor.timeout = 0.2
    executor.retries = 0

    started = time.monotonic()
    result = executor.call_sync(
        capability=f"test:blocking:{uuid4()}",
        primary=("slow_provider", lambda: time.sleep(3.0)),
    )
    elapsed = time.monotonic() - started

    # V1 ran sync providers on the event loop, so wait_for could never interrupt them and a hung
    # public-internet call blocked an indicator worker until its own socket gave up.
    assert result.ok is False
    assert result.errors[-1].code == "CAPABILITY_TIMEOUT"
    assert elapsed < 2.0


def test_last_known_good_scan_accepts_a_lookback_bound():
    reset_memory_repository()
    run_id = uuid4()
    snapshot_repository.save_factor_states(
        run_id, [_state("EQ01_CN_FUNDING_LIQUIDITY", 10)]
    )
    states = snapshot_repository.latest_factor_states(
        before=NOW + timedelta(hours=1), lookback_hours=4320.0
    )
    assert "EQ01_CN_FUNDING_LIQUIDITY" in states
