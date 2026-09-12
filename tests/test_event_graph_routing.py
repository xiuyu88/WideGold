"""Cost-contract tests for the production Event Intelligence graph.

The V1 incident that motivated these tests was 78 LLM calls producing 0 events.  Two properties
keep that from recurring: every call must be able to produce a schema-valid object (covered in
``test_llm_structured_contract``), and a cluster must not spend calls on nodes whose input is
already known to be empty (covered here).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from widegold.domain.enums import EventVerificationStatus, Horizon, ModelTier, SourceTier
from widegold.graphs.event_intelligence import production
from widegold.graphs.event_intelligence.graph import run_event_graph_with_meta
from widegold.schemas.events import (
    EventExtractionOutput,
    EventImpactAssessment,
    FactorMapping,
    FactorMappingOutput,
    NewsCluster,
    NewsDocument,
)
from widegold.schemas.llm import ModelExecution, StructuredLLMResult

BASE = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)


def _cluster(tiers: tuple[SourceTier, ...] = (SourceTier.B,)) -> NewsCluster:
    documents = [
        NewsDocument(
            document_id=uuid4(),
            source_id="TEST",
            source_tier=tier,
            title=f"央行公开市场操作与资金面变化{index}",
            content="人民银行开展逆回购操作，银行间资金面转松。",
            published_at=BASE + timedelta(minutes=index),
            retrieved_at=BASE + timedelta(minutes=index + 1),
        )
        for index, tier in enumerate(tiers)
    ]
    return NewsCluster(
        cluster_id=uuid4(),
        canonical_title=documents[0].title,
        documents=documents,
        first_seen_at=documents[0].published_at,
        last_seen_at=documents[-1].published_at,
    )


def _execution(task_type: str) -> ModelExecution:
    return ModelExecution(
        provider="test",
        model_alias="test_alias",
        resolved_model="test-model",
        tier=ModelTier.L2,
        task_type=task_type,
        reasoning_effort="none",
        started_at=BASE,
        finished_at=BASE,
        latency_ms=1,
        schema_valid=True,
        status="SUCCESS",
    )


def _install_fake_llm(monkeypatch, responses: dict[str, object]) -> list[str]:
    """Replace the LLM boundary with a scripted stub and record every task type invoked."""
    calls: list[str] = []

    class _FakeService:
        def structured_call_sync(self, task_type, messages, schema):
            calls.append(task_type)
            if task_type not in responses:
                raise AssertionError(f"unexpected LLM call for task_type={task_type}")
            return StructuredLLMResult(data=responses[task_type], execution=_execution(task_type))

    monkeypatch.setattr(production, "LLMService", _FakeService)
    return calls


def _mapping(factor_id: str = "EQ01_CN_FUNDING_LIQUIDITY") -> FactorMappingOutput:
    return FactorMappingOutput(
        mappings=[
            FactorMapping(
                factor_id=factor_id, direction=1, mapping_confidence=0.8, reason_tags=["funding"]
            )
        ]
    )


def _impact(strength: int) -> EventImpactAssessment:
    return EventImpactAssessment(
        strength=strength,
        confidence=0.7,
        novelty=0.5,
        priced_in=0.5,
        implementation=0.6,
        horizon=Horizon.SWING,
        half_life_days=3.0,
        reasoning_summary="test",
    )


def test_non_event_headline_costs_exactly_one_llm_call(monkeypatch):
    calls = _install_fake_llm(
        monkeypatch, {"extraction": EventExtractionOutput(event_detected=False)}
    )
    events, executions = run_event_graph_with_meta(_cluster(), mode="llm")

    assert events == []
    assert calls == ["extraction"]
    assert len(executions) == 1


def test_event_without_factor_mapping_stops_before_assessment(monkeypatch):
    calls = _install_fake_llm(
        monkeypatch,
        {
            "extraction": EventExtractionOutput(event_detected=True, event_type="CN_LIQUIDITY"),
            "reasoning": FactorMappingOutput(mappings=[], unmapped_reasons=["no clear factor"]),
        },
    )
    events, _ = run_event_graph_with_meta(_cluster(), mode="llm")

    assert events == []
    assert calls == ["extraction", "reasoning"]


def test_unknown_factor_ids_are_dropped_and_end_the_graph(monkeypatch):
    calls = _install_fake_llm(
        monkeypatch,
        {
            "extraction": EventExtractionOutput(event_detected=True, event_type="CN_LIQUIDITY"),
            "reasoning": _mapping("NOT_A_REAL_FACTOR"),
        },
    )
    events, _ = run_event_graph_with_meta(_cluster(), mode="llm")

    assert events == []
    assert calls == ["extraction", "reasoning"]


def test_material_event_completes_in_three_calls_without_expert_review(monkeypatch):
    calls: list[str] = []
    # "reasoning" serves both mapping and assessment; return the right object per invocation.
    sequence = [_mapping(), _impact(3)]

    class _SequencedService:
        def structured_call_sync(self, task_type, messages, schema):
            calls.append(task_type)
            if task_type == "extraction":
                return StructuredLLMResult(
                    data=EventExtractionOutput(event_detected=True, event_type="CN_LIQUIDITY"),
                    execution=_execution(task_type),
                )
            if task_type == "reasoning":
                return StructuredLLMResult(data=sequence.pop(0), execution=_execution(task_type))
            raise AssertionError(f"expert review must not run: {task_type}")

    monkeypatch.setattr(production, "LLMService", _SequencedService)

    events, executions = run_event_graph_with_meta(_cluster((SourceTier.B, SourceTier.C)), mode="llm")

    assert calls == ["extraction", "reasoning", "reasoning"]
    assert len(events) == 1
    assert len(executions) == 3
    assert events[0].verification_status == EventVerificationStatus.VERIFIED


def test_weak_event_with_validation_flag_is_downgraded_instead_of_escalated(monkeypatch):
    calls: list[str] = []
    sequence = [_mapping(), _impact(1)]

    class _SequencedService:
        def structured_call_sync(self, task_type, messages, schema):
            calls.append(task_type)
            if task_type == "extraction":
                return StructuredLLMResult(
                    data=EventExtractionOutput(event_detected=True, event_type="CN_LIQUIDITY"),
                    execution=_execution(task_type),
                )
            if task_type == "reasoning":
                return StructuredLLMResult(data=sequence.pop(0), execution=_execution(task_type))
            raise AssertionError("expert review is too expensive for a strength-1 event")

    monkeypatch.setattr(production, "LLMService", _SequencedService)
    events, _ = run_event_graph_with_meta(_cluster((SourceTier.D,)), mode="llm")

    assert calls == ["extraction", "reasoning", "reasoning"]
    assert len(events) == 1
    assert events[0].verification_status == EventVerificationStatus.UNVERIFIED


def test_routing_predicates_are_shared_by_both_execution_paths():
    # DirectEventGraph exists for environments without LangGraph; it must branch identically.
    from widegold.graphs.event_intelligence.graph import DirectEventGraph

    assert production.route_after_extract({"extracted_event": EventExtractionOutput(event_detected=False)}) == "finalize"
    assert production.route_after_mapping({"factor_mapping": FactorMappingOutput(mappings=[])}) == "finalize"
    assert production.route_after_conflict({"escalation_required": False}) == "finalize"
    assert DirectEventGraph("llm").mode == "llm"


@pytest.mark.parametrize("strength,expected_calls", [(4, 4), (2, 3)])
def test_expert_review_only_runs_for_material_contested_events(monkeypatch, strength, expected_calls):
    calls: list[str] = []
    sequence = [_mapping(), _impact(strength)]

    class _SequencedService:
        def structured_call_sync(self, task_type, messages, schema):
            calls.append(task_type)
            if task_type == "extraction":
                return StructuredLLMResult(
                    data=EventExtractionOutput(event_detected=True, event_type="CN_POLICY"),
                    execution=_execution(task_type),
                )
            if task_type == "reasoning":
                return StructuredLLMResult(data=sequence.pop(0), execution=_execution(task_type))
            from widegold.schemas.events import ExpertEventReview

            return StructuredLLMResult(
                data=ExpertEventReview(approved=True, review_summary="ok"),
                execution=_execution(task_type),
            )

    monkeypatch.setattr(production, "LLMService", _SequencedService)
    # Three distinct source tiers trigger the source-disagreement branch.
    cluster = _cluster((SourceTier.A, SourceTier.C, SourceTier.D))
    run_event_graph_with_meta(cluster, mode="llm")

    assert len(calls) == expected_calls
