from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    states: dict[str, CircuitState] = field(default_factory=dict)

    def allow(self, key: str, failure_threshold: int, cooldown_seconds: float) -> bool:
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
        self.states[key] = CircuitState()

    def failure(self, key: str, failure_threshold: int) -> None:
        state = self.states.setdefault(key, CircuitState())
        state.failures += 1
        if state.failures >= failure_threshold and state.opened_at_monotonic is None:
            state.opened_at_monotonic = time.monotonic()


circuit_breakers = CircuitBreakerRegistry()


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
        value = func()
        if inspect.isawaitable(value):
            return await asyncio.wait_for(value, timeout=self.timeout)
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
