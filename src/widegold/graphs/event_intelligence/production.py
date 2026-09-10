from __future__ import annotations

import json
from typing import Any

from widegold.domain.enums import EventVerificationStatus, SourceTier
from widegold.llm.service import LLMService
from widegold.schemas.events import (
    EventExtractionOutput,
    EventImpactAssessment,
    ExpertEventReview,
    FactorMappingOutput,
    StructuredEvent,
)
from widegold.settings.config import asset_config, factor_config


def _messages(system: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]


def extract_event_llm(state: dict) -> dict:
    cluster = state["cluster"]
    result = LLMService().structured_call_sync(
        "extraction",
        _messages(
            "你是金融事件抽取器。只抽取提供材料中的事实，不补充未知信息。输出必须符合JSON schema。",
            {
                "canonical_title": cluster.canonical_title,
                "documents": [
                    {"title": d.title, "content": d.content, "source_tier": d.source_tier.value, "published_at": d.published_at}
                    for d in cluster.documents
                ],
            },
        ),
        EventExtractionOutput,
    )
    return {"extracted_event": result.data, "llm_executions": [*state.get("llm_executions", []), result.execution]}


def map_factors_llm(state: dict) -> dict:
    extracted = state["extracted_event"]
    allowed = [
        {"factor_id": f["id"], "name": f["name"], "family": f["family"], "group": f["group"]}
        for f in factor_config()["factors"]
    ]
    result = LLMService().structured_call_sync(
        "reasoning",
        _messages(
            "将事件映射到允许的因子列表。只能使用给定factor_id；不确定时少映射而不是猜。direction=1表示事件使该因子对对应资产族更支持，-1表示更压制。",
            {"event": extracted.model_dump(mode="json"), "allowed_factors": allowed},
        ),
        FactorMappingOutput,
    )
    allowed_ids = {x["factor_id"] for x in allowed}
    clean = [m for m in result.data.mappings if m.factor_id in allowed_ids]
    output = result.data.model_copy(update={"mappings": clean})
    return {"factor_mapping": output, "llm_executions": [*state.get("llm_executions", []), result.execution]}


def assess_event_llm(state: dict) -> dict:
    result = LLMService().structured_call_sync(
        "reasoning",
        _messages(
            "评估金融事件影响。strength仅表示事件重要性1-5；priced_in无法判断时取0.5；implementation必须根据落地程度保守给值；half_life应与事件寿命一致。不要生成最终资产评分。",
            {
                "event": state["extracted_event"].model_dump(mode="json"),
                "factor_mapping": state["factor_mapping"].model_dump(mode="json"),
                "allowed_assets": [x["id"] for x in asset_config()["assets"]],
            },
        ),
        EventImpactAssessment,
    )
    allowed_assets = {x["id"] for x in asset_config()["assets"]}
    clean_overrides = {k: v for k, v in result.data.asset_override_candidates.items() if k in allowed_assets}
    assessment = result.data.model_copy(update={"asset_override_candidates": clean_overrides})
    return {"impact_assessment": assessment, "llm_executions": [*state.get("llm_executions", []), result.execution]}


def validate_event(state: dict) -> dict:
    errors: list[str] = list(state.get("errors", []))
    cluster = state["cluster"]
    extraction = state["extracted_event"]
    mapping = state["factor_mapping"]
    impact = state["impact_assessment"]
    if not extraction.event_detected:
        errors.append("NO_EVENT")
    if not mapping.mappings:
        errors.append("NO_FACTOR_MAPPING")
    if cluster.documents and all(d.source_tier == SourceTier.D for d in cluster.documents):
        errors.append("UNVERIFIED_D_TIER_ONLY")
    if impact.implementation >= 0.9 and any("传闻" in d.title for d in cluster.documents):
        errors.append("IMPLEMENTATION_TOO_HIGH_FOR_RUMOR")
    return {"errors": errors}


def detect_conflict(state: dict) -> dict:
    errors = state.get("errors", [])
    impact = state["impact_assessment"]
    high_impact = impact.strength >= 4
    source_disagreement = len({d.source_tier.value for d in state["cluster"].documents}) > 2
    required = bool(errors) or (high_impact and source_disagreement)
    return {
        "escalation_required": required,
        "escalation_reason": ",".join(errors) if errors else ("HIGH_IMPACT_SOURCE_DISAGREEMENT" if required else None),
    }


def expert_review(state: dict) -> dict:
    if not state.get("escalation_required"):
        return {}
    result = LLMService().structured_call_sync(
        "expert_review",
        _messages(
            "你是事件专家审查器。基于原始事实、映射、评估和冲突标记进行保守复核。不得新增未提供事实。",
            {
                "event": state["extracted_event"].model_dump(mode="json"),
                "mappings": state["factor_mapping"].model_dump(mode="json"),
                "impact": state["impact_assessment"].model_dump(mode="json"),
                "errors": state.get("errors", []),
            },
        ),
        ExpertEventReview,
    )
    return {"expert_review": result.data, "llm_executions": [*state.get("llm_executions", []), result.execution]}


def finalize_event_llm(state: dict) -> dict:
    extraction = state["extracted_event"]
    if not extraction.event_detected:
        return {"final_events": []}

    mapping = state["factor_mapping"]
    impact = state["impact_assessment"]
    review = state.get("expert_review")
    if review is not None:
        if not review.approved:
            return {"final_events": []}
        if review.mappings:
            mapping = mapping.model_copy(update={"mappings": review.mappings})
        if review.impact is not None:
            impact = review.impact

    cluster = state["cluster"]
    source_tier = min((d.source_tier for d in cluster.documents), key=lambda x: list(SourceTier).index(x))
    errors = state.get("errors", [])
    verification = EventVerificationStatus.VERIFIED
    if "UNVERIFIED_D_TIER_ONLY" in errors:
        verification = EventVerificationStatus.UNVERIFIED
    elif errors:
        verification = EventVerificationStatus.PARTIALLY_VERIFIED

    event = StructuredEvent(
        cluster_id=cluster.cluster_id,
        event_type=extraction.event_type or "UNKNOWN",
        canonical_title=extraction.canonical_title or cluster.canonical_title,
        published_at=cluster.first_seen_at,
        effective_at=extraction.effective_at,
        verification_status=verification,
        source_tier=source_tier,
        factors=mapping.mappings,
        strength=impact.strength,
        confidence=impact.confidence,
        novelty=impact.novelty,
        priced_in=impact.priced_in,
        implementation=impact.implementation,
        horizon=impact.horizon,
        half_life_days=impact.half_life_days,
        asset_overrides=impact.asset_override_candidates,
        reason_tags=[tag for m in mapping.mappings for tag in m.reason_tags],
        evidence_document_ids=[d.document_id for d in cluster.documents],
        model_execution_ids=[x.execution_id for x in state.get("llm_executions", [])],
        graph_version="1.0.0",
        prompt_version="1.0.0",
    )
    return {"final_events": [event]}
