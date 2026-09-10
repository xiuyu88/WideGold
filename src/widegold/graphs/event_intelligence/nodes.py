from widegold.domain.enums import EventVerificationStatus, Horizon
from widegold.schemas.events import (
    EventExtractionOutput,
    EventImpactAssessment,
    FactorMapping,
    FactorMappingOutput,
    StructuredEvent,
)
from widegold.graphs.event_intelligence.state import EventGraphState


def extract_event_mock(state: EventGraphState) -> EventGraphState:
    cluster = state["cluster"]
    title = cluster.canonical_title
    if "美联储" in title:
        event_type = "FED_POLICY"
        entities = ["Federal Reserve"]
        geography = ["US", "GLOBAL"]
    elif "资金面" in title or "PMI" in title:
        event_type = "CN_LIQUIDITY_GROWTH"
        entities = ["China"]
        geography = ["CN"]
    elif "半导体" in title:
        event_type = "CN_INDUSTRY_POLICY"
        entities = ["Semiconductor"]
        geography = ["CN"]
    else:
        event_type = "GENERAL_MARKET_EVENT"
        entities = []
        geography = []

    return {
        "extracted_event": EventExtractionOutput(
            event_detected=True,
            event_type=event_type,
            canonical_title=title,
            entities=entities,
            geography=geography,
            effective_at=cluster.last_seen_at,
            facts=[doc.title for doc in cluster.documents],
            uncertainties=[],
        )
    }


def map_factors_mock(state: EventGraphState) -> EventGraphState:
    event_type = state["extracted_event"].event_type
    mappings: list[FactorMapping]
    if event_type == "FED_POLICY":
        mappings = [
            FactorMapping(factor_id="G01_US_REAL_YIELD", direction=1, mapping_confidence=0.92, reason_tags=["real_yield_down"]),
            FactorMapping(factor_id="G02_USD", direction=1, mapping_confidence=0.88, reason_tags=["usd_weak"]),
            FactorMapping(factor_id="G03_FED_POLICY_SURPRISE", direction=1, mapping_confidence=0.94, reason_tags=["dovish_surprise"]),
            FactorMapping(factor_id="EQ10_GLOBAL_FIN_CONDITIONS", direction=1, mapping_confidence=0.82, reason_tags=["global_liquidity_support"]),
        ]
    elif event_type == "CN_LIQUIDITY_GROWTH":
        mappings = [
            FactorMapping(factor_id="EQ01_CN_FUNDING_LIQUIDITY", direction=1, mapping_confidence=0.93, reason_tags=["funding_easing"]),
            FactorMapping(factor_id="EQ04_CN_GROWTH_MOMENTUM", direction=-1, mapping_confidence=0.91, reason_tags=["pmi_weak"]),
        ]
    elif event_type == "CN_INDUSTRY_POLICY":
        mappings = [
            FactorMapping(factor_id="EQ08_POLICY_SUPPORT", direction=1, mapping_confidence=0.90, reason_tags=["official_policy_implemented"]),
        ]
    else:
        mappings = []
    return {"factor_mapping": FactorMappingOutput(mappings=mappings)}


def assess_event_mock(state: EventGraphState) -> EventGraphState:
    event_type = state["extracted_event"].event_type
    if event_type == "FED_POLICY":
        assessment = EventImpactAssessment(
            strength=4, confidence=0.90, novelty=0.75, priced_in=0.35, implementation=1.0,
            horizon=Horizon.TACTICAL, half_life_days=2.0,
            reasoning_summary="Mock: dovish surprise supports real-yield/USD channels.",
        )
    elif event_type == "CN_LIQUIDITY_GROWTH":
        assessment = EventImpactAssessment(
            strength=3, confidence=0.88, novelty=0.60, priced_in=0.45, implementation=1.0,
            horizon=Horizon.SWING, half_life_days=4.0,
            reasoning_summary="Mock: liquidity improves while growth confirmation remains weak.",
        )
    elif event_type == "CN_INDUSTRY_POLICY":
        assessment = EventImpactAssessment(
            strength=3, confidence=0.86, novelty=0.70, priced_in=0.40, implementation=0.90,
            horizon=Horizon.SWING, half_life_days=5.0,
            asset_override_candidates={"CHINEXT": 1.15, "STAR50": 1.35},
            reasoning_summary="Mock: implemented industry policy benefits growth/technology exposure more.",
        )
    else:
        assessment = EventImpactAssessment(
            strength=1, confidence=0.50, novelty=0.50, priced_in=0.50, implementation=0.50,
            horizon=Horizon.TACTICAL, half_life_days=1.0,
            reasoning_summary="Mock generic event.",
        )
    return {"impact_assessment": assessment}


def finalize_event_mock(state: EventGraphState) -> EventGraphState:
    cluster = state["cluster"]
    extracted = state["extracted_event"]
    impact = state["impact_assessment"]
    event = StructuredEvent(
        cluster_id=cluster.cluster_id,
        event_type=extracted.event_type or "UNKNOWN",
        canonical_title=extracted.canonical_title or cluster.canonical_title,
        published_at=cluster.first_seen_at,
        effective_at=extracted.effective_at,
        verification_status=EventVerificationStatus.VERIFIED,
        source_tier=cluster.documents[0].source_tier,
        factors=state["factor_mapping"].mappings,
        strength=impact.strength,
        confidence=impact.confidence,
        novelty=impact.novelty,
        priced_in=impact.priced_in,
        implementation=impact.implementation,
        horizon=impact.horizon,
        half_life_days=impact.half_life_days,
        asset_overrides=impact.asset_override_candidates,
        reason_tags=[tag for mapping in state["factor_mapping"].mappings for tag in mapping.reason_tags],
        evidence_document_ids=[doc.document_id for doc in cluster.documents],
    )
    return {"final_events": [event]}
