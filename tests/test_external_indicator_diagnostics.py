from datetime import date, datetime, timezone
from types import SimpleNamespace

import widegold.services.external_indicator_diagnostics as diag
from widegold.repositories.memory import reset_memory_repository, snapshot_repository
from widegold.schemas.indicators import IndicatorObservation

AS_OF = datetime(2026, 9, 10, 10, 10, tzinfo=timezone.utc)


def setup_function():
    reset_memory_repository()


def test_external_diagnostics_expand_asset_scoped_earnings_and_surface_capabilities(monkeypatch):
    monkeypatch.setattr(diag, "repository", lambda: snapshot_repository)
    monkeypatch.setattr(diag, "get_settings", lambda: SimpleNamespace(external_indicator_url="http://bridge"))
    rows = diag.external_indicator_diagnostics(as_of=AS_OF)
    ids = {(row["indicator_id"], row["asset_id"]) for row in rows}
    assert ("CN_DR007", None) in ids
    assert ("CN_INDEX_EARNINGS_REV", "CSI300") in ids
    assert ("CN_INDEX_EARNINGS_REV", "CSI1000") in ids
    dr007 = next(row for row in rows if row["indicator_id"] == "CN_DR007")
    assert dr007["capability"] == "china_money.dr007"
    assert dr007["bridge_configured"] is True
    assert dr007["operational_status"] == "MISSING"


def test_external_diagnostics_show_stored_last_known_good(monkeypatch):
    monkeypatch.setattr(diag, "repository", lambda: snapshot_repository)
    monkeypatch.setattr(diag, "get_settings", lambda: SimpleNamespace(external_indicator_url=None))
    snapshot_repository.save_indicator_observations([
        IndicatorObservation(
            indicator_id="CN_DR007",
            observation_date=date(2026, 9, 10),
            release_ts=AS_OF,
            ingest_ts=AS_OF,
            value=1.45,
            source_id="MCP_TEST",
        )
    ])
    rows = diag.external_indicator_diagnostics(as_of=AS_OF)
    dr007 = next(row for row in rows if row["indicator_id"] == "CN_DR007")
    assert dr007["operational_status"] == "AVAILABLE"
    assert dr007["last_source_id"] == "MCP_TEST"
