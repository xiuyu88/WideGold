from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictBridgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BridgeFetchRequest(StrictBridgeModel):
    contract_version: str
    capability: str
    indicator_id: str
    asset_id: str | None = None
    start_date: date
    end_date: date
    as_of: datetime
    force_refresh: bool = False
    source_hint: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
