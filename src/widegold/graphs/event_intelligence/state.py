from typing import TypedDict

from widegold.schemas.common import AnalysisContext
from widegold.schemas.events import (
    EventExtractionOutput,
    EventImpactAssessment,
    ExpertEventReview,
    FactorMappingOutput,
    NewsCluster,
    StructuredEvent,
)
from widegold.schemas.llm import ModelExecution


class EventGraphState(TypedDict, total=False):
    analysis_context: AnalysisContext
    cluster: NewsCluster
    extracted_event: EventExtractionOutput
    factor_mapping: FactorMappingOutput
    impact_assessment: EventImpactAssessment
    errors: list[str]
    escalation_required: bool
    escalation_reason: str | None
    expert_review: ExpertEventReview
    llm_executions: list[ModelExecution]
    final_events: list[StructuredEvent]
