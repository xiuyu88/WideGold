from datetime import datetime
from typing import Any

from pydantic import Field

from widegold.domain.enums import DataStatus
from widegold.schemas.common import StrictModel


class CapabilityError(StrictModel):
    code: str
    message: str
    retryable: bool = True
    capability: str
    provider: str | None = None


class CapabilityResult(StrictModel):
    capability: str
    ok: bool
    provider: str | None = None
    data: Any | None = None
    status: DataStatus = DataStatus.VALID
    attempts: int = 1
    fallback_used: bool = False
    started_at: datetime
    finished_at: datetime
    errors: list[CapabilityError] = Field(default_factory=list)


class FactorInput(StrictModel):
    factor_id: str
    asset_id: str | None = None
    value: float | None = Field(default=None, ge=-100, le=100)
    observed_at: datetime | None = None
    status: DataStatus = DataStatus.UNAVAILABLE
    reliability: float = Field(default=0.0, ge=0, le=1)
    source_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FactorResolutionSummary(StrictModel):
    total_factors: int
    state_rows: int | None = None
    valid: int
    stale: int
    partial: int
    unavailable: int
    conflicted: int
    warnings: list[str] = Field(default_factory=list)


class AssetCoverage(StrictModel):
    asset_id: str
    weighted_coverage: float = Field(ge=0, le=1)
    fresh_weighted_coverage: float = Field(ge=0, le=1)
    last_known_good_weighted_ratio: float = Field(ge=0, le=1)
    critical_coverage: float = Field(ge=0, le=1)
    unavailable_factor_ratio: float = Field(ge=0, le=1)
    scoreable: bool
    missing_critical: list[str] = Field(default_factory=list)


class QualityGateResult(StrictModel):
    passed_for_preview: bool
    passed_for_publish: bool
    assets: list[AssetCoverage]
    overall_weighted_coverage: float = Field(ge=0, le=1)
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
