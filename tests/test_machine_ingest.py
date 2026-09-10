from datetime import date, datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from apps.api.main import app
import apps.api.routers.ingest as ingest_router
from widegold.repositories.memory import reset_memory_repository


def setup_function():
    reset_memory_repository()


def _payload():
    ts = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc).isoformat()
    return [{
        "indicator_id": "CN_DR007",
        "observation_date": date(2026, 9, 10).isoformat(),
        "release_ts": ts,
        "ingest_ts": ts,
        "value": 1.45,
        "source_id": "MCP_GATEWAY",
        "metadata": {"benchmark": "DR007"},
    }]


def test_machine_ingest_requires_configured_service_key(monkeypatch):
    monkeypatch.setattr(ingest_router, "get_settings", lambda: SimpleNamespace(ingest_api_key=None))
    client = TestClient(app)
    response = client.post("/api/v1/ingest/indicators", json=_payload())
    assert response.status_code == 503


def test_machine_ingest_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(ingest_router, "get_settings", lambda: SimpleNamespace(ingest_api_key="secret"))
    client = TestClient(app)
    response = client.post(
        "/api/v1/ingest/indicators",
        headers={"Authorization": "Bearer wrong"},
        json=_payload(),
    )
    assert response.status_code == 401


def test_machine_ingest_accepts_bearer_key(monkeypatch):
    monkeypatch.setattr(ingest_router, "get_settings", lambda: SimpleNamespace(ingest_api_key="secret"))
    client = TestClient(app)
    response = client.post(
        "/api/v1/ingest/indicators",
        headers={"Authorization": "Bearer secret"},
        json=_payload(),
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 1
