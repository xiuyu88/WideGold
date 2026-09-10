from __future__ import annotations

from typing import Any, Awaitable, Callable

from widegold.resilience.executor import ResilientExecutor
from widegold.schemas.resilience import CapabilityResult


class ToolGateway:
    """Unified fault boundary for MCP tools and ordinary external tool callables.

    The project intentionally does not depend on one MCP SDK. An MCP client can register its tool
    invocation as an async callable, while tests and non-MCP providers use the exact same contract.
    """

    def __init__(self, executor: ResilientExecutor | None = None) -> None:
        self.executor = executor or ResilientExecutor()
        self._tools: dict[str, list[tuple[str, Callable[[], Any | Awaitable[Any]]]]] = {}

    def register(
        self,
        capability: str,
        provider: str,
        call: Callable[[], Any | Awaitable[Any]],
    ) -> None:
        self._tools.setdefault(capability, []).append((provider, call))

    async def invoke(self, capability: str) -> CapabilityResult:
        candidates = self._tools.get(capability, [])
        if not candidates:
            raise KeyError(f"No tool registered for capability {capability}")
        return await self.executor.call(capability, candidates[0], candidates[1:])
