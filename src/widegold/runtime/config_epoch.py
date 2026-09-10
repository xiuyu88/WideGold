from __future__ import annotations

from widegold.observability.logging import get_logger
from widegold.runtime.redis_client import redis_client

logger = get_logger("widegold.runtime_config")
CONFIG_GENERATION_KEY = "widegold:runtime-config:generation"


def read_runtime_config_generation() -> int | None:
    try:
        raw = redis_client().get(CONFIG_GENERATION_KEY)
        return int(raw) if raw is not None else 0
    except Exception:
        # Config invalidation is an acceleration mechanism. Runtime DB lookups and short local TTLs
        # remain authoritative, so Redis failure must not make the service unavailable.
        logger.warning("Runtime config generation read failed", exc_info=True)
        return None


def bump_runtime_config_generation() -> int | None:
    try:
        return int(redis_client().incr(CONFIG_GENERATION_KEY))
    except Exception:
        logger.warning("Runtime config generation bump failed; local TTL will refresh configs", exc_info=True)
        return None
