from __future__ import annotations

import secrets
from dataclasses import dataclass

from widegold.observability.logging import get_logger
from widegold.runtime.redis_client import redis_client
from widegold.settings.app import get_settings

logger = get_logger("widegold.lock")

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


@dataclass
class DistributedLock:
    key: str
    token: str | None = None
    acquired: bool = False
    degraded: bool = False

    def acquire(self) -> bool:
        settings = get_settings()
        if not settings.run_lock_enabled:
            self.acquired = True
            return True
        self.token = secrets.token_urlsafe(18)
        try:
            self.acquired = bool(
                redis_client().set(self.key, self.token, nx=True, ex=settings.run_lock_ttl_seconds)
            )
            return self.acquired
        except Exception as exc:
            # Lock is an availability helper, not the source of truth. Prefect concurrency and DB
            # snapshots still protect the system, so a Redis outage degrades rather than kills runs.
            self.acquired = True
            self.degraded = True
            logger.warning("Redis run-lock unavailable; continuing in degraded mode", exc_info=True)
            return True

    def release(self) -> None:
        if not self.token or self.degraded or not get_settings().run_lock_enabled:
            return
        try:
            redis_client().eval(_RELEASE_SCRIPT, 1, self.key, self.token)
        except Exception:
            logger.warning("Redis run-lock release failed; TTL will recover the key", exc_info=True)


def analysis_lock_key(analysis_date: str, mode: str) -> str:
    return f"widegold:analysis-lock:{analysis_date}:{mode}"
