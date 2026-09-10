from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from widegold.external_bridge.adapters import AdapterResult
from widegold.external_bridge.models import BridgeFetchRequest
from widegold.external_bridge.service import ExternalBridgeService


def _request(capability: str = "china_money.dr007") -> BridgeFetchRequest:
    now = datetime.now(timezone.utc)
    return BridgeFetchRequest(
        contract_version="widegold.external-indicator.v1",
        capability=capability,
        indicator_id="CN_DR007",
        asset_id=None,
        start_date=date.today() - timedelta(days=30),
        end_date=date.today(),
        as_of=now,
        force_refresh=False,
        source_hint="CHINAMONEY_AKSHARE",
        params={},
    )


def test_external_bridge_service_drops_future_release(tmp_path, monkeypatch):
    monkeypatch.setenv("WIDEGOLD_EXTERNAL_BRIDGE_DB", str(tmp_path / "bridge.sqlite3"))
    service = ExternalBridgeService()
    req = _request()

    class FakeAdapter:
        def fetch(self, request):
            return AdapterResult(
                "TEST",
                "VALID",
                [
                    {
                        "observation_date": date.today(),
                        "value": 1.5,
                        "release_ts": request.as_of + timedelta(minutes=1),
                        "metadata": {"benchmark": "DR007"},
                    }
                ],
                [],
            )

    service.adapters[req.capability] = FakeAdapter()
    result = service.fetch(req)
    assert result["status"] == "UNAVAILABLE"
    assert result["observations"] == []
    assert "bridge_dropped_release_after_as_of" in result["warnings"]


def test_final_compose_contains_external_bridge_and_prefect_ui():
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert "external-bridge" in services
    assert services["external-bridge"]["healthcheck"]
    assert (
        services["prefect-server"]["environment"]["PREFECT_UI_API_URL"]
        == "http://localhost:${PREFECT_PORT:-4200}/api"
    )


def test_final_env_defaults_to_raw_indicators_and_free_bridge():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".env.final.example").read_text(encoding="utf-8")
    assert "WIDEGOLD_DATA_MODE=raw_indicators" in text
    assert "WIDEGOLD_EXTERNAL_INDICATOR_URL=http://external-bridge:9100" in text
    assert "WIDEGOLD_SMOKE_MAX_POLL_SECONDS=600" in text
    assert "TUSHARE_TOKEN" not in text
    assert "WIND_TOKEN" not in text


def test_indicator_registry_final_version_and_free_sources():
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "configs" / "indicators.yaml").read_text(encoding="utf-8"))
    assert str(cfg["version"]) == "1.6.0"
    by_id = {row["id"]: row for row in cfg["indicators"]}
    assert by_id["CN_DR007"]["source_id"] == "CHINAMONEY_AKSHARE"
    assert by_id["CN_INDEX_EARNINGS_REV"]["source_id"] == "EASTMONEY_AKSHARE"
    assert by_id["CN_FOREIGN_ACTIVITY"]["source_id"] == "EASTMONEY_HKEX_PROXY"
    assert by_id["GLOBAL_CENTRAL_BANK_GOLD"]["source_id"] == "WGC"
    assert by_id["GOLD_SUPPLY_DEMAND"]["source_id"] == "WGC"
