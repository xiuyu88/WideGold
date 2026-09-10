from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from widegold.domain.enums import DataStatus
from widegold.schemas.common import StrictModel


class FactorHealthState(StrictModel):
    factor_id: str
    asset_id: str | None = None
    as_of_ts: datetime
    state: float = Field(ge=-100, le=100)
    reliability: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    status: DataStatus
    quality_flags: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    event_count: int = 0


class FactorHealthItem(StrictModel):
    factor_id: str
    name: str
    family: str
    scope: str
    evidence: str | None = None
    aggregate_status: DataStatus
    min_reliability: float = Field(ge=0, le=1)
    min_coverage: float = Field(ge=0, le=1)
    states: list[FactorHealthState]
    quality_flags: list[str] = Field(default_factory=list)


class FactorHealthSummary(StrictModel):
    logical_factors: int
    physical_states: int
    valid: int
    stale: int
    partial: int
    unavailable: int
    conflicted: int
    average_reliability: float = Field(ge=0, le=1)
    average_coverage: float = Field(ge=0, le=1)


class FactorHealthResponse(StrictModel):
    analysis_run_id: UUID
    analysis_date: str | None = None
    status: str | None = None
    source: str
    summary: FactorHealthSummary
    factors: list[FactorHealthItem]
