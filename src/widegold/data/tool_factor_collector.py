from __future__ import annotations

from datetime import datetime
from typing import Any

from widegold.domain.enums import DataStatus
from widegold.schemas.resilience import FactorInput
from widegold.tools.gateway import ToolGateway


class ToolFactorCollector:
    """Turns MCP/tool capabilities into normalized FactorInput rows.

    A tool may fail independently. The failed factor becomes UNAVAILABLE and the resolver decides
    whether to use last-known-good data or leave it out of scoring.
    """

    def __init__(self, gateway: ToolGateway):
        self.gateway = gateway

    async def collect(self, bindings: dict[str, str], as_of: datetime) -> list[FactorInput]:
        rows: list[FactorInput] = []
        for factor_id, capability in bindings.items():
            result = await self.gateway.invoke(capability)
            if not result.ok:
                rows.append(FactorInput(
                    factor_id=factor_id,
                    value=None,
                    observed_at=as_of,
                    status=DataStatus.UNAVAILABLE,
                    reliability=0.0,
                    warnings=[f"{err.code}:{err.provider or 'unknown'}" for err in result.errors],
                ))
                continue
            payload: Any = result.data
            if isinstance(payload, dict):
                value = payload.get("value")
                observed_at = payload.get("observed_at", as_of)
                reliability = float(payload.get("reliability", 0.8))
                source_ids = payload.get("source_ids", [result.provider] if result.provider else [])
            else:
                value = payload
                observed_at = as_of
                reliability = 0.8
                source_ids = [result.provider] if result.provider else []
            rows.append(FactorInput(
                factor_id=factor_id,
                value=float(value),
                observed_at=observed_at,
                status=DataStatus.VALID,
                reliability=reliability,
                source_ids=source_ids,
            ))
        return rows
