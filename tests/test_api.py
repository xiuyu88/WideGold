from fastapi.testclient import TestClient

from apps.api.main import app


def test_mock_api_roundtrip():
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    run = client.post("/api/v1/mock/run")
    assert run.status_code == 200
    payload = run.json()
    assert len(payload["assets"]) == 7
    current = client.get("/api/v1/dashboard/current")
    assert current.status_code == 200
    assert current.json()["analysis_run_id"] == payload["analysis_run_id"]


def test_dashboard_preview_does_not_replace_published(monkeypatch):
    from uuid import uuid4

    from apps.api.routers import dashboard
    from widegold.repositories.memory import InMemorySnapshotRepository
    from widegold.schemas.scores import DashboardSnapshot

    client = TestClient(app)
    published = DashboardSnapshot.model_validate(client.post('/api/v1/mock/run').json())
    repo = InMemorySnapshotRepository()
    repo.save(published)
    preview = published.model_copy(update={
        'analysis_run_id': uuid4(), 'analysis_date': '2099-01-01',
        'status': 'PREVIEW_READY', 'published': False,
    })
    repo.save(preview)
    monkeypatch.setattr(dashboard, 'repository', lambda: repo)

    response = client.get('/api/v1/dashboard/latest-preview')
    assert response.status_code == 200
    assert response.json()['analysis_run_id'] == str(preview.analysis_run_id)
    assert response.json()['published'] is False
    assert repo.latest_published().analysis_run_id == published.analysis_run_id


def test_admin_trace_api_is_cursor_based():
    client = TestClient(app)
    run = client.post("/api/v1/mock/run")
    assert run.status_code == 200
    run_id = run.json()["analysis_run_id"]

    first = client.get(f"/api/v1/admin/runs/{run_id}/trace?after_seq=0&limit=3")
    assert first.status_code == 200
    payload = first.json()
    assert 1 <= len(payload["events"]) <= 3
    assert payload["next_seq"] >= payload["events"][-1]["trace_seq"]

    second = client.get(
        f"/api/v1/admin/runs/{run_id}/trace?after_seq={payload['next_seq']}&limit=100"
    )
    assert second.status_code == 200
    assert all(e["trace_seq"] > payload["next_seq"] for e in second.json()["events"])


def test_readiness_and_admin_diagnostics_expose_config_contract():
    client = TestClient(app)
    ready = client.get('/ready')
    assert ready.status_code == 200
    assert ready.json()['config']['factor_count'] == 27
    diag = client.get('/api/v1/admin/system/diagnostics')
    assert diag.status_code == 200
    payload = diag.json()
    assert payload['config']['valid'] is True
    assert payload['config']['external_indicator_count'] >= 1


def test_public_asset_history_and_events_are_from_published_snapshot():
    client = TestClient(app)
    run = client.post("/api/v1/mock/run")
    assert run.status_code == 200
    payload = run.json()

    history = client.get("/api/v1/assets/CSI300/history")
    assert history.status_code == 200
    points = history.json()["points"]
    assert points
    assert points[-1]["analysis_run_id"] == payload["analysis_run_id"]

    events = client.get("/api/v1/events")
    assert events.status_code == 200
    event_rows = events.json()["items"]
    assert event_rows
    event_id = event_rows[0]["event_id"]
    detail = client.get(f"/api/v1/events/{event_id}")
    assert detail.status_code == 200
    assert detail.json()["event"]["event_id"] == event_id


def test_asset_history_rejects_unknown_asset():
    client = TestClient(app)
    response = client.get("/api/v1/assets/NOT_REAL/history")
    assert response.status_code == 404


def test_admin_run_list_and_diagnostics_have_operational_summary():
    client = TestClient(app)
    client.post("/api/v1/mock/run")
    runs = client.get("/api/v1/admin/runs?limit=5")
    assert runs.status_code == 200
    assert runs.json()["items"]
    diag = client.get("/api/v1/admin/system/diagnostics")
    assert diag.status_code == 200
    payload = diag.json()
    assert payload["external_summary"]["total"] == len(payload["external_indicators"])
    assert "recent_runs" in payload


def test_admin_can_stage_and_activate_an_approved_runtime_config_version():
    from copy import deepcopy

    from widegold.settings.config import load_bootstrap_yaml

    client = TestClient(app)
    content = deepcopy(load_bootstrap_yaml("assets.yaml"))
    content["version"] = "1.0.91-test"
    staged = client.post(
        "/api/v1/admin/config/versions",
        json={
            "config_type": "ASSET_REGISTRY",
            "version": content["version"],
            "content": content,
            "status": "APPROVED",
        },
    )
    assert staged.status_code == 201
    assert staged.json()["validation"]["valid"] is True

    activated = client.post(
        "/api/v1/admin/config/activate",
        json={"config_type": "ASSET_REGISTRY", "version": content["version"]},
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"


def test_admin_cannot_activate_draft_config_version():
    from copy import deepcopy

    from widegold.settings.config import load_bootstrap_yaml

    client = TestClient(app)
    content = deepcopy(load_bootstrap_yaml("news.yaml"))
    content["version"] = "1.1.91-test"
    staged = client.post(
        "/api/v1/admin/config/versions",
        json={
            "config_type": "NEWS_COLLECTION",
            "version": content["version"],
            "content": content,
            "status": "DRAFT",
        },
    )
    assert staged.status_code == 201
    activated = client.post(
        "/api/v1/admin/config/activate",
        json={"config_type": "NEWS_COLLECTION", "version": content["version"]},
    )
    assert activated.status_code == 409


def test_admin_rejects_invalid_approved_config_candidate():
    from copy import deepcopy

    from widegold.settings.config import load_bootstrap_yaml

    client = TestClient(app)
    content = deepcopy(load_bootstrap_yaml("weights.yaml"))
    content["version"] = "1.0.92-test"
    content["equity_sensitivity"]["EQ01_CN_FUNDING_LIQUIDITY"]["UNKNOWN_ASSET"] = 1.0
    response = client.post(
        "/api/v1/admin/config/versions",
        json={
            "config_type": "WEIGHTS",
            "version": content["version"],
            "content": content,
            "status": "APPROVED",
        },
    )
    assert response.status_code == 422


def test_admin_audit_endpoint_returns_structured_governance_events():
    from widegold.repositories.factory import repository

    repo = repository()
    repo.append_audit(
        actor_user_id=None,
        action="TEST_AUDIT_EVENT",
        resource_type="test_resource",
        resource_id="audit-contract",
        after={"ok": True},
    )
    client = TestClient(app)
    response = client.get("/api/v1/admin/audit?limit=20")
    assert response.status_code == 200
    rows = response.json()["items"]
    row = next(item for item in rows if item["action"] == "TEST_AUDIT_EVENT")
    assert row["resource_type"] == "test_resource"
    assert row["resource_id"] == "audit-contract"
    assert row["after"]["ok"] is True
    assert row["created_at"]


def test_admin_factor_health_exposes_logical_and_physical_factor_state_health():
    client = TestClient(app)
    run = client.post('/api/v1/mock/run')
    assert run.status_code == 200
    run_id = run.json()['analysis_run_id']

    response = client.get(f'/api/v1/admin/factors/health?run_id={run_id}')
    assert response.status_code == 200
    payload = response.json()
    assert payload['analysis_run_id'] == run_id
    assert payload['summary']['logical_factors'] == 27
    assert payload['summary']['physical_states'] >= 27
    assert len(payload['factors']) == 27
    trend = next(x for x in payload['factors'] if x['factor_id'] == 'EQ14_TREND_MOMENTUM')
    assert len(trend['states']) >= 1
    assert all(0 <= state['reliability'] <= 1 for state in trend['states'])
