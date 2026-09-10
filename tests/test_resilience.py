import asyncio

from widegold.resilience.executor import ResilientExecutor, circuit_breakers


def test_capability_failure_uses_fallback():
    circuit_breakers.states.clear()
    calls = {"primary": 0, "fallback": 0}

    def broken():
        calls["primary"] += 1
        raise RuntimeError("mcp unavailable")

    def fallback():
        calls["fallback"] += 1
        return {"value": 42}

    result = asyncio.run(
        ResilientExecutor().call(
            "market.real_yield",
            ("mcp_primary", broken),
            [("http_fallback", fallback)],
        )
    )
    assert result.ok is True
    assert result.provider == "http_fallback"
    assert result.fallback_used is True
    assert result.data == {"value": 42}
    assert calls["primary"] >= 1
    assert calls["fallback"] == 1
