from datetime import date, datetime, timezone

from widegold.domain.enums import DataStatus
from widegold.repositories.memory import reset_memory_repository, snapshot_repository
from widegold.schemas.indicators import IndicatorObservation, IndicatorSeries
from widegold.services.indicator_collection import IndicatorCollectionService

AS_OF = datetime(2026, 9, 10, 18, 10, tzinfo=timezone.utc)


class GoodProvider:
    def fetch(self, request, spec):
        return IndicatorSeries(
            indicator_id=request.indicator_id,
            source_id="TEST",
            status=DataStatus.VALID,
            observations=[
                IndicatorObservation(
                    indicator_id=request.indicator_id,
                    observation_date=date(2026, 9, 9),
                    release_ts=AS_OF,
                    ingest_ts=AS_OF,
                    value=42.0,
                    source_id="TEST",
                )
            ],
        )


class EmptyProvider:
    def fetch(self, request, spec):
        return IndicatorSeries(
            indicator_id=request.indicator_id,
            source_id="TEST",
            status=DataStatus.UNAVAILABLE,
            warnings=["empty"],
        )


def setup_function():
    reset_memory_repository()


def test_collection_isolates_unavailable_indicator_and_keeps_success():
    specs = {
        "GOOD": {"id": "GOOD", "provider": "good", "source_id": "TEST", "lookback_days": 30},
        "EMPTY": {"id": "EMPTY", "provider": "empty", "source_id": "TEST", "lookback_days": 30},
        "EXTERNAL": {"id": "EXTERNAL", "provider": "external", "source_id": "MCP"},
    }
    external = IndicatorObservation(
        indicator_id="EXTERNAL",
        observation_date=date(2026, 9, 8),
        release_ts=AS_OF,
        ingest_ts=AS_OF,
        value=9.0,
        source_id="MCP",
    )
    snapshot_repository.save_indicator_observations([external])

    providers = {"good": GoodProvider(), "empty": EmptyProvider()}
    service = IndicatorCollectionService(
        repo=snapshot_repository,
        specs=specs,
        provider_factory=lambda name: providers[name],
        max_workers=3,
    )
    result = service.collect({("GOOD", None), ("EMPTY", None), ("EXTERNAL", None)}, AS_OF)

    assert result.histories[("GOOD", None)][-1].value == 42.0
    assert result.histories[("EXTERNAL", None)][-1].value == 9.0
    assert result.histories[("EMPTY", None)] == []
    assert result.fetched == 1
    assert result.external_only == 1
    assert result.unavailable == 1


def test_repeated_same_observation_does_not_create_pseudo_vintage():
    item1 = IndicatorObservation(
        indicator_id="GOOD",
        observation_date=date(2026, 9, 8),
        release_ts=datetime(2026, 9, 9, tzinfo=timezone.utc),
        ingest_ts=AS_OF,
        value=10.0,
        source_id="TEST",
    )
    item2 = item1.model_copy(update={"release_ts": AS_OF, "ingest_ts": AS_OF})
    snapshot_repository.save_indicator_observations([item1, item2])
    rows = snapshot_repository._indicator_observations[("GOOD", None)]
    assert len(rows) == 1


def test_external_indicator_can_be_fetched_through_normalized_bridge(monkeypatch):
    from types import SimpleNamespace
    import widegold.services.indicator_collection as collection_module

    specs = {
        "EXTERNAL": {"id": "EXTERNAL", "provider": "external", "source_id": "MCP"},
    }
    monkeypatch.setattr(
        collection_module,
        "get_settings",
        lambda: SimpleNamespace(external_indicator_url="http://bridge"),
    )
    providers = {"external_bridge": GoodProvider()}
    service = IndicatorCollectionService(
        repo=snapshot_repository,
        specs=specs,
        provider_factory=lambda name: providers[name],
        max_workers=1,
    )
    result = service.collect({("EXTERNAL", None)}, AS_OF)
    assert result.fetched == 1
    assert result.histories[("EXTERNAL", None)][-1].value == 42.0


def test_force_refresh_reaches_indicator_provider_request():
    seen = {}

    class CaptureProvider:
        def fetch(self, request, spec):
            seen["force_refresh"] = request.force_refresh
            return GoodProvider().fetch(request, spec)

    service = IndicatorCollectionService(
        repo=snapshot_repository,
        specs={"GOOD": {"id": "GOOD", "provider": "capture", "source_id": "TEST"}},
        provider_factory=lambda name: CaptureProvider(),
        max_workers=1,
    )
    service.collect({("GOOD", None)}, AS_OF, force_refresh=True)
    assert seen["force_refresh"] is True



def test_external_bridge_contract_rejection_falls_back_without_persisting_bad_value(monkeypatch):
    from datetime import date, datetime, timezone
    from types import SimpleNamespace
    from widegold.domain.enums import DataStatus
    from widegold.schemas.indicators import IndicatorObservation, IndicatorSeries
    import widegold.services.indicator_collection as module

    class BadBridge:
        def fetch(self, request, spec):
            now = request.as_of
            return IndicatorSeries(
                indicator_id=request.indicator_id,
                source_id="BAD_BRIDGE",
                observations=[IndicatorObservation(
                    indicator_id="CN_DR007", observation_date=now.date(), release_ts=now,
                    ingest_ts=now, value=145.0, source_id="BAD_BRIDGE",
                )],
                status=DataStatus.VALID,
            )

    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(external_indicator_url="http://bridge"))
    service = IndicatorCollectionService(
        provider_factory=lambda name: BadBridge(),
        specs={"CN_DR007": {
            "id":"CN_DR007", "provider":"external", "frequency":"daily", "lookback_days":30,
            "external_params":{"expected_range":[0.0,20.0], "asset_scoped":False, "unit":"percent"},
        }},
        max_workers=1,
    )
    result = service.collect({("CN_DR007", None)}, datetime(2026,9,10,10,tzinfo=timezone.utc))
    assert result.histories[("CN_DR007", None)] == []
    assert result.unavailable == 1
    assert any("indicator_contract_rejected:CN_DR007" in w for w in result.warnings)
