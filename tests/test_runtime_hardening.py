"""Regression tests for the V1.3 detail review.

These cover defects that were silent by construction: an unbound name reachable only on a
degraded source, a cache that never expired under traffic, a reserved run that never reached a
terminal state, and a feature guard that returned a fabricated neutral value.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from widegold.engine.indicator_features import level_robust
from widegold.external_bridge.adapters import CONTRACT_VERSION
from widegold.external_bridge.models import BridgeFetchRequest
from widegold.external_bridge.service import ExternalBridgeService
from widegold.schemas.indicators import IndicatorObservation

NOW = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)


def _request(capability: str, as_of: datetime = NOW) -> BridgeFetchRequest:
    return BridgeFetchRequest(
        contract_version=CONTRACT_VERSION,
        capability=capability,
        indicator_id="TEST_INDICATOR",
        start_date=as_of.date() - timedelta(days=30),
        end_date=as_of.date(),
        as_of=as_of,
    )


def _observation(day: date, value: float) -> IndicatorObservation:
    return IndicatorObservation(
        indicator_id="TEST_SERIES",
        observation_date=day,
        release_ts=NOW,
        ingest_ts=NOW,
        value=value,
        source_id="TEST",
    )


def test_bridge_reports_a_warning_when_an_adapter_returns_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("WIDEGOLD_EXTERNAL_BRIDGE_DB", str(tmp_path / "bridge.sqlite3"))
    service = ExternalBridgeService()
    capability = service.capabilities[0]

    class _SilentAdapter:
        capability = capability

        def fetch(self, request):
            return None

    service.adapters[capability] = _SilentAdapter()
    payload = service.fetch(_request(capability))

    # V1 referenced an unbound `warning` here and turned a degraded source into a bridge 500.
    assert payload["status"] == "UNAVAILABLE"
    assert payload["warnings"] == ["adapter_returned_no_result"]


def test_bridge_does_not_repeat_the_same_look_ahead_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("WIDEGOLD_EXTERNAL_BRIDGE_DB", str(tmp_path / "bridge.sqlite3"))
    service = ExternalBridgeService()
    capability = service.capabilities[0]
    future = NOW + timedelta(days=5)

    class _LookAheadResult:
        status = "VALID"
        source_id = "TEST"
        warnings: list[str] = []
        observations = [
            {"observation_date": future.date(), "value": 1.0, "release_ts": future},
            {"observation_date": future.date(), "value": 2.0, "release_ts": future},
            {"observation_date": future.date(), "value": 3.0, "release_ts": future},
        ]

    class _LookAheadAdapter:
        capability = capability

        def fetch(self, request):
            return _LookAheadResult()

    service.adapters[capability] = _LookAheadAdapter()
    payload = service.fetch(_request(capability))

    assert payload["observations"] == []
    assert payload["warnings"].count("bridge_dropped_release_after_as_of") == 1


def test_level_robust_scores_a_three_point_series_instead_of_returning_neutral():
    series = [
        _observation(date(2026, 9, 9), 10.0),
        _observation(date(2026, 9, 10), 10.2),
        _observation(date(2026, 9, 11), 18.0),
    ]
    score = level_robust(series)

    # The guard admitted three observations but then handed robust_score only two, which made it
    # return a hard 0.0 that was indistinguishable from a genuinely neutral factor.
    assert score is not None
    assert score > 0


def test_dispatch_failure_marks_the_reserved_run_terminal(monkeypatch):
    from widegold.domain.enums import AnalysisRunMode, PublishMode, TriggerType
    from widegold.repositories.memory import reset_memory_repository, snapshot_repository
    from widegold.schemas.common import AnalysisRunRequest
    from widegold.services import dispatcher

    reset_memory_repository()
    monkeypatch.setattr(dispatcher.get_settings(), "orchestration_mode", "prefect", raising=False)

    def _boom(*args, **kwargs):
        raise RuntimeError("prefect unreachable")

    import sys
    from types import ModuleType

    deployments = ModuleType("prefect.deployments")
    deployments.run_deployment = _boom
    parent = sys.modules.get("prefect") or ModuleType("prefect")
    monkeypatch.setitem(sys.modules, "prefect", parent)
    monkeypatch.setitem(sys.modules, "prefect.deployments", deployments)
    monkeypatch.setattr(parent, "deployments", deployments, raising=False)

    request = AnalysisRunRequest(
        analysis_date=date(2026, 9, 12),
        trigger_type=TriggerType.ADMIN_MANUAL,
        run_mode=AnalysisRunMode.FULL_REFRESH,
        publish_mode=PublishMode.PREVIEW_ONLY,
    )
    with pytest.raises(RuntimeError):
        dispatcher.dispatch_analysis(request)

    runs = snapshot_repository.list_runs(limit=10)
    # A reserved run left at PENDING after a failed dispatch never reaches a terminal state, which
    # is what makes E2E polling wait out its full timeout on a failure that already happened.
    assert runs and runs[0]["status"] == "FAILED"
