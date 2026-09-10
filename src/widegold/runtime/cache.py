from __future__ import annotations

from widegold.observability.logging import get_logger
from widegold.runtime.redis_client import redis_client
from widegold.schemas.scores import DashboardSnapshot
from widegold.settings.app import get_settings

logger = get_logger("widegold.cache")
CURRENT_KEY = "widegold:dashboard:current"


def get_current_snapshot() -> DashboardSnapshot | None:
    if not get_settings().dashboard_cache_enabled:
        return None
    try:
        raw = redis_client().get(CURRENT_KEY)
        return DashboardSnapshot.model_validate_json(raw) if raw else None
    except Exception:
        logger.warning("Dashboard cache read failed; falling back to PostgreSQL", exc_info=True)
        return None


def set_current_snapshot(snapshot: DashboardSnapshot) -> None:
    if not get_settings().dashboard_cache_enabled or not snapshot.published:
        return
    try:
        redis_client().setex(
            CURRENT_KEY,
            get_settings().dashboard_cache_ttl_seconds,
            snapshot.model_dump_json(),
        )
    except Exception:
        logger.warning("Dashboard cache write failed; PostgreSQL remains authoritative", exc_info=True)


def invalidate_current_snapshot() -> None:
    if not get_settings().dashboard_cache_enabled:
        return
    try:
        redis_client().delete(CURRENT_KEY)
    except Exception:
        logger.warning("Dashboard cache invalidation failed", exc_info=True)
