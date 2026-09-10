from types import SimpleNamespace

import widegold.services.runtime_health as health


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement):
        return SimpleNamespace(scalar_one=lambda: 1)


class FakeEngine:
    def connect(self):
        return FakeConnection()


def _settings(**overrides):
    base = dict(
        persistence="memory",
        auth_mode="disabled",
        run_lock_enabled=False,
        dashboard_cache_enabled=False,
        orchestration_mode="direct",
        prefect_api_url="http://localhost:4200/api",
        event_graph_mode="mock",
        deepseek_api_key=None,
        qwen_api_key=None,
        qwen_base_url=None,
        gpt_compat_api_key=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_readiness_skips_optional_dependencies_in_memory_mode(monkeypatch):
    monkeypatch.setattr(health, "get_settings", lambda: _settings())
    result = health.runtime_readiness()
    assert result.ready is True
    assert {x.name: x.status for x in result.checks}["postgres"] == "SKIPPED"
    assert {x.name: x.status for x in result.checks}["redis"] == "SKIPPED"


def test_readiness_requires_postgres_and_redis_in_production_shape(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: _settings(persistence="postgres", auth_mode="session", run_lock_enabled=True),
    )
    monkeypatch.setattr(health, "engine", lambda: FakeEngine())
    monkeypatch.setattr(health, "redis_ping", lambda: True)
    result = health.runtime_readiness()
    assert result.ready is True
    assert {x.name: x.status for x in result.checks}["postgres"] == "UP"
    assert {x.name: x.status for x in result.checks}["redis"] == "UP"


def test_readiness_fails_closed_when_required_redis_is_down(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: _settings(persistence="memory", auth_mode="session"),
    )
    monkeypatch.setattr(health, "redis_ping", lambda: False)
    result = health.runtime_readiness()
    assert result.ready is False
    assert {x.name: x.status for x in result.checks}["redis"] == "DOWN"


def test_prefect_and_llm_failures_are_degraded_not_api_readiness_failures(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: _settings(
            orchestration_mode="prefect",
            prefect_api_url="http://prefect:4200/api",
            event_graph_mode="llm",
            deepseek_api_key=None,
            qwen_api_key=None,
            qwen_base_url=None,
            gpt_compat_api_key=None,
        ),
    )
    monkeypatch.setattr(health, "_http_ok", lambda *args, **kwargs: False)
    result = health.runtime_readiness()
    by_name = {x.name: x for x in result.checks}
    assert result.ready is True
    assert result.degraded is True
    assert by_name["prefect"].required is False
    assert by_name["prefect"].status == "DOWN"
    assert by_name["llm_deepseek_config"].status == "DOWN"


def test_prefect_diagnostic_up_when_api_is_reachable(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: _settings(
            orchestration_mode="prefect",
            prefect_api_url="http://prefect:4200/api",
            event_graph_mode="mock",
        ),
    )
    monkeypatch.setattr(health, "_http_ok", lambda *args, **kwargs: True)
    result = health.runtime_readiness()
    assert {x.name: x.status for x in result.checks}["prefect"] == "UP"
    assert result.ready is True
