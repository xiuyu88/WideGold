from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from widegold.domain.enums import EventVerificationStatus, Horizon, SourceTier
from widegold.schemas.common import StrictModel


class NewsDocument(StrictModel):
    document_id: UUID = Field(default_factory=uuid4)
    source_id: str
    source_tier: SourceTier
    title: str
    content: str
    published_at: datetime
    retrieved_at: datetime
    url: str | None = None
    language: str = "zh-CN"


class NewsCluster(StrictModel):
    cluster_id: UUID = Field(default_factory=uuid4)
    canonical_title: str
    documents: list[NewsDocument]
    first_seen_at: datetime
    last_seen_at: datetime


class EventExtractionOutput(StrictModel):
    event_detected: bool
    event_type: str | None = None
    canonical_title: str | None = None
    entities: list[str] = Field(default_factory=list)
    geography: list[str] = Field(default_factory=list)
    effective_at: datetime | None = None
    facts: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class FactorMapping(StrictModel):
    factor_id: str
    direction: Literal[-1, 1]
    mapping_confidence: float = Field(ge=0, le=1)
    reason_tags: list[str] = Field(default_factory=list)


class FactorMappingOutput(StrictModel):
    mappings: list[FactorMapping] = Field(default_factory=list)
    unmapped_reasons: list[str] = Field(default_factory=list)


class EventImpactAssessment(StrictModel):
    strength: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    priced_in: float = Field(ge=0, le=1)
    implementation: float = Field(ge=0, le=1)
    horizon: Horizon
    half_life_days: float = Field(ge=0)
    asset_override_candidates: dict[str, float] = Field(default_factory=dict)
    reasoning_summary: str


class StructuredEvent(StrictModel):
    event_id: UUID = Field(default_factory=uuid4)
    cluster_id: UUID | None = None
    event_type: str
    canonical_title: str
    published_at: datetime
    effective_at: datetime | None = None
    verification_status: EventVerificationStatus
    source_tier: SourceTier
    factors: list[FactorMapping]
    strength: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    priced_in: float = Field(ge=0, le=1)
    implementation: float = Field(ge=0, le=1)
    horizon: Horizon
    half_life_days: float = Field(ge=0)
    asset_overrides: dict[str, float] = Field(default_factory=dict)
    reason_tags: list[str] = Field(default_factory=list)
    evidence_document_ids: list[UUID] = Field(default_factory=list)
    model_execution_ids: list[UUID] = Field(default_factory=list)
    graph_version: str = "mock-1.0.0"
    prompt_version: str = "mock-1.0.0"


class ExpertEventReview(StrictModel):
    approved: bool
    mappings: list[FactorMapping] = Field(default_factory=list)
    impact: EventImpactAssessment | None = None
    conflict_resolved: bool = False
    review_summary: str
