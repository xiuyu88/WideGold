from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from copy import deepcopy
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Any, Iterator

from sqlalchemy import select

from widegold.settings.app import get_settings


@dataclass(frozen=True)
class ConfigBinding:
    config_type: str
    snapshot_attr: str


CONFIG_BINDINGS: dict[str, ConfigBinding] = {
    "assets.yaml": ConfigBinding("ASSET_REGISTRY", "assets"),
    "factors.yaml": ConfigBinding("FACTOR_SCHEMA", "factor_schema"),
    "weights.yaml": ConfigBinding("WEIGHTS", "weights"),
    "rules.yaml": ConfigBinding("EVENT_RULES", "event_rules"),
    "models.yaml": ConfigBinding("MODEL_ROUTING", "model_routing"),
    "factor_runtime.yaml": ConfigBinding("DATA_DEFINITION", "data_definition"),
    "indicators.yaml": ConfigBinding("INDICATOR_REGISTRY", "indicator_registry"),
    "factor_calculators.yaml": ConfigBinding("FACTOR_CALCULATORS", "factor_calculators"),
    "news.yaml": ConfigBinding("NEWS_COLLECTION", "news_collection"),
    "resilience.yaml": ConfigBinding("RESILIENCE", "resilience"),
    "evaluation.yaml": ConfigBinding("EVALUATION", "evaluation"),
    "calendar_cn.yaml": ConfigBinding("CALENDAR", "calendar"),
}

_override: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "widegold_runtime_config_override", default=None
)
_cache_lock = Lock()
_active_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_seen_generation: int | None = None
_next_generation_poll_at: float = 0.0


def _source() -> str:
    source = get_settings().runtime_config_source.lower().strip()
    if source == "auto":
        return "db" if get_settings().persistence.lower() == "postgres" else "yaml"
    return source


def _fetch(config_type: str, version: str | None = None) -> dict[str, Any] | None:
    # Lazy imports prevent settings/config <-> ORM initialization cycles.
    from widegold.db.models import ConfigVersionModel
    from widegold.db.session import db_session

    with db_session() as session:
        stmt = select(ConfigVersionModel).where(ConfigVersionModel.config_type == config_type)
        if version is None:
            stmt = stmt.where(ConfigVersionModel.status == "ACTIVE")
        else:
            stmt = stmt.where(ConfigVersionModel.version == version)
        row = session.execute(stmt).scalar_one_or_none()
        return deepcopy(row.content_json) if row is not None else None


def clear_runtime_config_cache() -> None:
    with _cache_lock:
        _active_cache.clear()


def _sync_generation_if_needed() -> None:
    """Invalidate process-local config cache after another process activates a version.

    Redis only carries a generation counter; PostgreSQL remains the source of truth. If Redis is
    unavailable, the short local TTL still converges automatically.
    """
    global _seen_generation, _next_generation_poll_at
    if _source() != "db":
        return
    now = monotonic()
    with _cache_lock:
        if now < _next_generation_poll_at:
            return
        _next_generation_poll_at = now + max(
            0.25, float(get_settings().runtime_config_generation_poll_seconds)
        )
    try:
        from widegold.runtime.config_epoch import read_runtime_config_generation

        generation = read_runtime_config_generation()
    except Exception:
        return
    if generation is None:
        return
    with _cache_lock:
        if _seen_generation is None:
            _seen_generation = generation
        elif generation != _seen_generation:
            _active_cache.clear()
            _seen_generation = generation
            try:
                from widegold.services.config_validation import validate_runtime_config
                validate_runtime_config.cache_clear()
            except Exception:
                # Readiness validation is auxiliary; config retrieval must stay available even if
                # the validator is not importable during early process initialization.
                pass


def runtime_config_content(name: str) -> dict[str, Any] | None:
    scoped = _override.get()
    if scoped is not None and name in scoped:
        return deepcopy(scoped[name])

    binding = CONFIG_BINDINGS.get(name)
    if binding is None or _source() == "yaml":
        return None

    _sync_generation_if_needed()
    now = monotonic()
    with _cache_lock:
        cached = _active_cache.get(name)
        if cached is not None and cached[0] > now:
            return deepcopy(cached[1])

    try:
        content = _fetch(binding.config_type)
    except Exception:
        if get_settings().runtime_config_source.lower() == "db":
            raise
        return None

    if content is None:
        if get_settings().runtime_config_source.lower() == "db":
            raise RuntimeError(f"No ACTIVE runtime config for {binding.config_type}")
        return None

    ttl = max(0.25, float(get_settings().runtime_config_cache_ttl_seconds))
    with _cache_lock:
        _active_cache[name] = (now + ttl, deepcopy(content))
    return content



def active_runtime_version_map() -> dict[str, str] | None:
    """Return one transactionally consistent ACTIVE version snapshot in DB mode.

    Returning ``None`` means runtime config is YAML-backed.  In strict DB mode every YAML-backed
    runtime config must have exactly one ACTIVE row; otherwise a new Analysis Run is rejected
    before doing any provider or LLM work.
    """
    if _source() != "db":
        return None
    from widegold.db.models import ConfigVersionModel
    from widegold.db.session import db_session

    type_to_attr = {binding.config_type: binding.snapshot_attr for binding in CONFIG_BINDINGS.values()}
    type_to_attr["SCORING_ENGINE"] = "scoring"
    with db_session() as session:
        rows = session.execute(
            select(ConfigVersionModel).where(ConfigVersionModel.status == "ACTIVE")
        ).scalars().all()
    versions: dict[str, str] = {}
    for row in rows:
        attr = type_to_attr.get(row.config_type)
        if attr is not None:
            versions[attr] = str(row.version)
    if get_settings().runtime_config_source.lower() == "db":
        missing = sorted(
            binding.snapshot_attr
            for binding in CONFIG_BINDINGS.values()
            if binding.snapshot_attr not in versions
        )
        if missing:
            raise RuntimeError(f"Missing ACTIVE runtime config versions: {','.join(missing)}")
    return versions


def activate_version_snapshot(snapshot) -> Token:
    """Freeze exact DB config contents for the lifetime of one execution stage.

    Prefect tasks call this independently using the same VersionSnapshot. That keeps an in-flight
    analysis deterministic even if an administrator activates a newer config version mid-run.
    """
    if _source() == "yaml":
        return _override.set({})

    contents: dict[str, dict[str, Any]] = {}
    strict = get_settings().runtime_config_source.lower() == "db"
    for name, binding in CONFIG_BINDINGS.items():
        version = getattr(snapshot, binding.snapshot_attr, None)
        if not version:
            continue
        try:
            content = _fetch(binding.config_type, str(version))
        except Exception:
            if strict:
                raise
            continue
        if content is None:
            if strict:
                raise RuntimeError(
                    f"Frozen runtime config missing: {binding.config_type} version {version}"
                )
            continue
        contents[name] = content
    return _override.set(contents)


def reset_version_snapshot(token: Token) -> None:
    _override.reset(token)


@contextmanager
def version_snapshot_scope(snapshot) -> Iterator[None]:
    token = activate_version_snapshot(snapshot)
    try:
        yield
    finally:
        reset_version_snapshot(token)
