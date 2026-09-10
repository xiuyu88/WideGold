from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from widegold.schemas.common import StrictModel


class ResearchRequest(StrictModel):
    research_request_id: UUID = Field(default_factory=uuid4)
    request_type: Literal[
        "new_factor", "factor_review", "rule_review", "weight_hypothesis", "source_review"
    ] = "factor_review"
    target_factor_id: str | None = None
    title: str
    question: str
    requested_by: UUID | None = None
    expert_review_required: bool = True


class ResearchEvidence(StrictModel):
    evidence_id: UUID = Field(default_factory=uuid4)
    research_request_id: UUID
    source_type: str = "web"
    source_tier: str = "C"
    title: str
    url: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime
    supports: bool | None = None
    claim_summary: str
    evidence_summary: str
    quality_score: float = Field(ge=0, le=1)
    metadata: dict = Field(default_factory=dict)


class ResearchSynthesis(StrictModel):
    hypothesis: str
    mechanism: str
    supportive_evidence: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evidence_grade: Literal["A", "B", "C", "D"]
    proposed_config_patch: dict = Field(default_factory=dict)
    validation_required: list[str] = Field(default_factory=lambda: ["walk_forward", "manual_review"])


class FactorResearchCandidate(StrictModel):
    candidate_id: UUID = Field(default_factory=uuid4)
    research_request_id: UUID
    target_factor_id: str | None = None
    candidate_type: Literal[
        "NEW_FACTOR", "LOGIC_CHANGE", "SOURCE_CHANGE", "WEIGHT_HYPOTHESIS", "CONFLICT_RULE"
    ]
    hypothesis: str
    mechanism: str
    evidence_grade: Literal["A", "B", "C", "D"]
    proposed_config_patch: dict = Field(default_factory=dict)
    validation_required: list[str] = Field(default_factory=list)
    status: str = "REVIEW_REQUIRED"
    production_enabled: bool = False


class ResearchFlowResult(StrictModel):
    research_request_id: UUID
    candidate_ids: list[UUID]
    evidence_count: int
    status: str
    human_review_required: bool = True


class ResearchReviewCommand(StrictModel):
    action: Literal["approve_shadow", "reject", "revise"]
    comment: str | None = None
    patch_override: dict | None = None
