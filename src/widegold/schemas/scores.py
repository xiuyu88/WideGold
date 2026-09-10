from datetime import datetime
from uuid import UUID

from pydantic import Field

from widegold.schemas.common import StrictModel, VersionSnapshot


class FactorContribution(StrictModel):
    factor_id: str
    group: str
    factor_state: float
    sensitivity: float
    reliability: float
    raw_contribution: float
    after_conflict: float
    after_group_cap: float
    rank_positive: int | None = None
    rank_negative: int | None = None


class ConfidenceResult(StrictModel):
    confidence: float = Field(ge=0, le=100)
    coverage_score: float = Field(ge=0, le=100)
    source_quality_score: float = Field(ge=0, le=100)
    agreement_score: float = Field(ge=0, le=100)
    stability_score: float = Field(ge=0, le=100)
    context_certainty_score: float = Field(ge=0, le=100)
    penalties: list[str] = Field(default_factory=list)
    caps_applied: list[str] = Field(default_factory=list)


class AssetScore(StrictModel):
    asset_score_id: UUID
    asset_id: str
    asset_name: str
    as_of_ts: datetime
    tactical_score: float = Field(ge=0, le=100)
    swing_score: float = Field(ge=0, le=100)
    strategic_score: float = Field(ge=0, le=100)
    direction_score: float = Field(ge=-100, le=100)
    score: float = Field(ge=0, le=100)
    label: str
    confidence: ConfidenceResult
    contributions: list[FactorContribution]
    top_positive: list[str]
    top_negative: list[str]
    risk_flags: list[str]
    versions: VersionSnapshot


class AssetExplanation(StrictModel):
    asset_id: str
    summary: str
    direction_reason: str
    top_positive: list[str]
    top_negative: list[str]
    biggest_risk: str
    confidence_explanation: str
    user_level: str = "beginner"


class DashboardSnapshot(StrictModel):
    analysis_run_id: UUID
    analysis_date: str
    as_of: datetime
    status: str
    published: bool
    assets: list[AssetScore]
    explanations: dict[str, AssetExplanation]
    top_events: list[dict]
    warnings: list[str] = Field(default_factory=list)
    factor_resolution: dict = Field(default_factory=dict)
    quality_gate: dict = Field(default_factory=dict)
