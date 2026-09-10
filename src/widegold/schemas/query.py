from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from widegold.schemas.common import StrictModel
from widegold.schemas.events import StructuredEvent


class AssetHistoryPoint(StrictModel):
    analysis_run_id: UUID
    analysis_date: str
    as_of: datetime
    score: float
    label: str
    confidence: float
    tactical_score: float
    swing_score: float
    strategic_score: float


class AssetHistoryResponse(StrictModel):
    asset_id: str
    points: list[AssetHistoryPoint] = Field(default_factory=list)


class EventListItem(StrictModel):
    event_id: UUID
    analysis_run_id: UUID
    canonical_title: str
    event_type: str
    published_at: datetime
    source_tier: str
    strength: int
    confidence: float
    factor_ids: list[str] = Field(default_factory=list)
    reason_tags: list[str] = Field(default_factory=list)


class EventListResponse(StrictModel):
    analysis_run_id: UUID
    items: list[EventListItem] = Field(default_factory=list)


class EvidenceDocument(StrictModel):
    document_id: UUID
    source_id: str
    source_tier: str
    title: str
    url: str | None = None
    published_at: datetime
    retrieved_at: datetime
    excerpt: str


class EventDetailResponse(StrictModel):
    analysis_run_id: UUID
    event: StructuredEvent
    evidence: list[EvidenceDocument] = Field(default_factory=list)
