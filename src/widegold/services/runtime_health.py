from __future__ import annotations

from time import perf_counter
from urllib.request import urlopen

from sqlalchemy import text

from widegold.db.session import engine
from widegold.runtime.redis_client import redis_ping
from widegold.schemas.system import DependencyCheck, RuntimeReadiness
from widegold.services.config_validation import validate_runtime_config
from widegold.settings.app import get_settings


def _timed(name: str, required: bool, func) -> DependencyCheck:
    started = perf_counter()
    try:
        ok = bool(func())
        message = None if ok else f"{name} check returned false"
    except Exception as exc:  # readiness must report dependency failure, not crash the endpoint
        ok = False
        message = str(exc)
    return DependencyCheck(
        name=name,
        status="UP" if ok else "DOWN",
        required=required,
        latency_ms=round((perf_counter() - started) * 1000, 2),
        message=message,
    )



def _http_ok(url: str, timeout: float = 2.0) -> bool:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - URL is operator-controlled config
        return 200 <= int(response.status) < 300


def _configured(value: str | None) -> bool:
    return bool(value and value.strip())

def runtime_readiness() -> RuntimeReadiness:
    settings = get_settings()
    config = validate_runtime_config()
    checks: list[DependencyCheck] = [
        DependencyCheck(
            name="config",
            status="UP" if config.valid else "DOWN",
            required=True,
            latency_ms=0.0,
            message=None if config.valid else "Runtime configuration validation failed",
        )
    ]

    if settings.persistence.lower() == "postgres":
        def db_check() -> bool:
            with engine().connect() as conn:
                return conn.execute(text("SELECT 1")).scalar_one() == 1
        checks.append(_timed("postgres", True, db_check))
    else:
        checks.append(DependencyCheck(name="postgres", status="SKIPPED", required=False, latency_ms=0.0))

    redis_required = (
        settings.auth_mode.lower() == "session"
        or settings.run_lock_enabled
        or settings.dashboard_cache_enabled
    )
    if redis_required:
        checks.append(_timed("redis", True, redis_ping))
    else:
        checks.append(DependencyCheck(name="redis", status="SKIPPED", required=False, latency_ms=0.0))

    # The API must remain able to serve the last Published snapshot even if Prefect is down,
    # so orchestration/model checks are diagnostic (non-required) rather than readiness killers.
    if settings.orchestration_mode.lower() == "prefect":
        prefect_health = f"{settings.prefect_api_url.rstrip('/')}/health"
        checks.append(_timed("prefect", False, lambda: _http_ok(prefect_health)))
    else:
        checks.append(DependencyCheck(name="prefect", status="SKIPPED", required=False, latency_ms=0.0))

    if settings.event_graph_mode.lower() == "llm":
        checks.append(DependencyCheck(
            name="llm_deepseek_config",
            status="UP" if _configured(settings.deepseek_api_key) else "DOWN",
            required=False, latency_ms=0.0,
            message=None if _configured(settings.deepseek_api_key) else "DeepSeek API key is not configured; event reasoning will degrade.",
        ))
        checks.append(DependencyCheck(
            name="llm_qwen_config",
            status="UP" if (_configured(settings.qwen_api_key) and _configured(settings.qwen_base_url)) else "DOWN",
            required=False, latency_ms=0.0,
            message=None if (_configured(settings.qwen_api_key) and _configured(settings.qwen_base_url)) else "Qwen is not configured; extraction will fall back to DeepSeek.",
        ))
        checks.append(DependencyCheck(
            name="llm_gpt_expert_config",
            status="UP" if _configured(settings.gpt_compat_api_key) else "DOWN",
            required=False, latency_ms=0.0,
            message=None if _configured(settings.gpt_compat_api_key) else "GPT expert is not configured; expert review will fall back to DeepSeek high.",
        ))

    ready = all(check.status == "UP" for check in checks if check.required)
    degraded = ready and any(check.status == "DOWN" for check in checks if not check.required)
    return RuntimeReadiness(ready=ready, degraded=degraded, checks=checks, config=config)
