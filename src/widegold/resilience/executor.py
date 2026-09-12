from __future__ import annotations

import asyncio
import contextvars
import inspect
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Awaitable, Callable

from widegold.domain.enums import DataStatus
from widegold.schemas.resilience import CapabilityError, CapabilityResult
from widegold.settings.config import resilience_config

CallableCapability = Callable[[], Any | Awaitable[Any]]


@dataclass
class CircuitState:
    failures: int = 0
    opened_at_monotonic: float | None = None


@dataclass
class CircuitBreakerRegistry:
    """Process-wide breaker state.

    Indicator collection fans out over a thread pool, so every method here is a read-modify-write
    under concurrency.  A single lock keeps the failure counters exact; contention is irrelevant
    because the critical sections are a few dict operations.
    """

    states: dict[str, CircuitState] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def allow(self, key: str, failure_threshold: int, cooldown_seconds: float) -> bool:
        with self._lock:
            state = self.states.setdefault(key, CircuitState())
            if state.failures < failure_threshold:
                return True
            if state.opened_at_monotonic is None:
                state.opened_at_monotonic = time.monotonic()
                return False
            if time.monotonic() - state.opened_at_monotonic >= cooldown_seconds:
                state.failures = 0
                state.opened_at_monotonic = None
                return True
            return False

    def success(self, key: str) -> None:
        with self._lock:
            self.states[key] = CircuitState()

    def failure(self, key: str, failure_threshold: int) -> None:
        with self._lock:
            state = self.states.setdefault(key, CircuitState())
            state.failures += 1
            if state.failures >= failure_threshold and state.opened_at_monotonic is None:
                state.opened_at_monotonic = time.monotonic()


circuit_breakers = CircuitBreakerRegistry()

_POOL_LOCK = Lock()
_CAPABILITY_POOL: ThreadPoolExecutor | None = None


def _capability_pool() -> ThreadPoolExecutor:
    """Shared pool for blocking capability calls.

    Deliberately long-lived and never shut down during a process's life: a call abandoned by the
    timeout keeps its thread until the underlying socket gives up, and that must not delay the
    analysis that already moved on.
    """
    global _CAPABILITY_POOL
    with _POOL_LOCK:
        if _CAPABILITY_POOL is None:
            size = int(os.getenv("WIDEGOLD_CAPABILITY_POOL_SIZE", "16"))
            _CAPABILITY_POOL = ThreadPoolExecutor(
                max_workers=max(4, size), thread_name_prefix="widegold-capability"
            )
        return _CAPABILITY_POOL


class ResilientExecutor:
    """Fault isolation for HTTP providers, MCP-like tools, search tools and model adapters.

    A capability failure returns structured state instead of crashing the whole analysis flow.
    """

    def __init__(self) -> None:
        cfg = resilience_config()["executor"]
        self.timeout = float(cfg["timeout_seconds"])
        self.retries = int(cfg["retries"])
        self.backoff = float(cfg["retry_backoff_seconds"])
        breaker = cfg["circuit_breaker"]
        self.failure_threshold = int(breaker["failure_threshold"])
        self.cooldown = float(breaker["cooldown_seconds"])

    async def _invoke(self, func: CallableCapability) -> Any:
        """Run one capability so that the configured timeout can actually fire.

        Most providers here are blocking (akshare, requests/httpx sync clients).  Calling them
        directly on the event loop makes ``asyncio.wait_for`` decorative: the loop is blocked
        until the provider returns on its own, so ``timeout_seconds`` never applies and one hung
        public-internet call can stall a whole indicator worker.  Running the sync call on a
        worker thread lets the await be cancelled on time.  The context is copied explicitly so the
        frozen runtime-config snapshot survives the hop, and a dedicated pool is used instead of
        the loop's default executor because ``asyncio.run`` would otherwise block at teardown
        waiting for exactly the hung call the timeout just abandoned.
        """
        if inspect.iscoroutinefunction(func):
            return await func()
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        value = await loop.run_in_executor(_capability_pool(), context.run, func)
        if inspect.isawaitable(value):
            return await value
        return value

    async def call(
        self,
        capability: str,
        primary: tuple[str, CallableCapability],
        fallbacks: list[tuple[str, CallableCapability]] | None = None,
    ) -> CapabilityResult:
        started = datetime.now(timezone.utc)
        candidates = [primary, *(fallbacks or [])]
        errors: list[CapabilityError] = []
        total_attempts = 0

        for candidate_index, (provider, func) in enumerate(candidates):
            breaker_key = f"{capability}:{provider}"
            if not circuit_breakers.allow(breaker_key, self.failure_threshold, self.cooldown):
                errors.append(CapabilityError(
                    code="CIRCUIT_OPEN",
                    message="Circuit breaker is open; provider skipped.",
                    retryable=True,
                    capability=capability,
                    provider=provider,
                ))
                continue

            for attempt in range(self.retries + 1):
                total_attempts += 1
                try:
                    data = await asyncio.wait_for(self._invoke(func), timeout=self.timeout)
                    circuit_breakers.success(breaker_key)
                    return CapabilityResult(
                        capability=capability,
                        ok=True,
                        provider=provider,
                        data=data,
                        status=DataStatus.VALID,
                        attempts=total_attempts,
                        fallback_used=candidate_index > 0,
                        started_at=started,
                        finished_at=datetime.now(timezone.utc),
                        errors=errors,
                    )
                except asyncio.TimeoutError:
                    exc: Exception = TimeoutError(f"{provider} timed out")
                    code = "CAPABILITY_TIMEOUT"
                except Exception as caught:  # noqa: BLE001 - boundary intentionally isolates providers
                    exc = caught
                    code = "CAPABILITY_FAILED"

                circuit_breakers.failure(breaker_key, self.failure_threshold)
                errors.append(CapabilityError(
                    code=code,
                    message=str(exc),
                    retryable=True,
                    capability=capability,
                    provider=provider,
                ))
                if attempt < self.retries:
                    await asyncio.sleep(self.backoff * (2**attempt))

        return CapabilityResult(
            capability=capability,
            ok=False,
            provider=None,
            data=None,
            status=DataStatus.UNAVAILABLE,
            attempts=total_attempts,
            fallback_used=len(candidates) > 1,
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            errors=errors,
        )

    def call_sync(
        self,
        capability: str,
        primary: tuple[str, CallableCapability],
        fallbacks: list[tuple[str, CallableCapability]] | None = None,
    ) -> CapabilityResult:
        return asyncio.run(self.call(capability, primary, fallbacks))
