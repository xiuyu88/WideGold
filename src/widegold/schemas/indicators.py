from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import Field

from widegold.domain.enums import DataStatus
from widegold.schemas.common import StrictModel


class IndicatorObservation(StrictModel):
    indicator_id: str
    asset_id: str | None = None
    observation_date: date
    release_ts: datetime
    ingest_ts: datetime
    value: float
    source_id: str
    revision_vintage: str = "latest"
    definition_version: str = "1.0.0"
    status: DataStatus = DataStatus.VALID
    metadata: dict[str, Any] = Field(default_factory=dict)


class IndicatorSeries(StrictModel):
    indicator_id: str
    source_id: str
    observations: list[IndicatorObservation] = Field(default_factory=list)
    status: DataStatus = DataStatus.VALID
    warnings: list[str] = Field(default_factory=list)


class IndicatorFetchRequest(StrictModel):
    indicator_id: str
    asset_id: str | None = None
    start_date: date
    end_date: date
    as_of: datetime
    force_refresh: bool = False


class ExternalBridgeObservation(StrictModel):
    """One normalized observation returned by an External/MCP bridge."""

    observation_date: date
    value: float
    release_ts: datetime
    ingest_ts: datetime | None = None
    source_id: str | None = None
    asset_id: str | None = None
    revision_vintage: str = "latest"
    definition_version: str | None = None
    status: DataStatus = DataStatus.VALID
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalBridgeResponse(StrictModel):
    """Frozen response contract for out-of-process indicator collectors."""

    contract_version: str
    capability: str
    source_id: str
    status: DataStatus = DataStatus.VALID
    observations: list[ExternalBridgeObservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
