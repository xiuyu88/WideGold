from __future__ import annotations

from functools import lru_cache

from widegold.settings.app import get_settings


@lru_cache(maxsize=1)
def redis_client():
    try:
        from redis import Redis
    except ImportError as exc:  # pragma: no cover - installed in production image
        raise RuntimeError("redis package is required for Redis runtime features") from exc
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def redis_ping() -> bool:
    try:
        return bool(redis_client().ping())
    except Exception:
        return False
