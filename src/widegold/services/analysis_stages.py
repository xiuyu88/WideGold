from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from widegold.domain.enums import AnalysisStatus, PublishMode
from widegold.engine.confidence import calculate_confidence
from widegold.engine.factor_resolution import resolve_factor_states
from widegold.engine.quality import run_quality_gate
from widegold.engine.rules import evaluate_conflicts
from widegold.engine.scoring import calculate_asset_score
from widegold.graphs.explanation.graph import build_explanation_graph
from widegold.schemas.common import VersionSnapshot
from widegold.schemas.events import StructuredEvent
from widegold.schemas.factors import FactorState
from widegold.schemas.resilience import FactorInput, FactorResolutionSummary, QualityGateResult
from widegold.schemas.scores import AssetExplanation, AssetScore
from widegold.settings.config import asset_config


@dataclass(frozen=True)
class FactorResolutionStageResult:
    states: list[FactorState]
    summary: FactorResolutionSummary


@dataclass(frozen=True)
class ScoringStageResult:
    quality: QualityGateResult
    scores: list[AssetScore]
    risk_flags_by_asset: dict[str, list[str]]
    confidence_penalty_by_asset: dict[str, float]


def resolve_factor_stage(
    *,
    factor_inputs: list[FactorInput],
    events: list[StructuredEvent],
    as_of: datetime,
    previous_states: dict[str, FactorState],
    event_source_available: bool,
) -> FactorResolutionStageResult:
    states, summary = resolve_factor_states(
        factor_inputs,
        events,
        as_of,
        previous_states=previous_states,
        event_source_available=event_source_available,
    )
    return FactorResolutionStageResult(states=states, summary=summary)


def score_stage(*, factor_states: list[FactorState], versions: VersionSnapshot) -> ScoringStageResult:
    quality = run_quality_gate(factor_states)
    coverage_by_asset = {item.asset_id: item for item in quality.assets}
    scores: list[AssetScore] = []
    risk_flags_by_asset: dict[str, list[str]] = {}
    confidence_penalty_by_asset: dict[str, float] = {}

    for item in asset_config()["assets"]:
        asset_id = item["id"]
        effects = evaluate_conflicts(factor_states, asset_id=asset_id)
        score = calculate_asset_score(
            asset_id,
            factor_states,
            effects,
            calculate_confidence,
            versions=versions,
        )
        coverage = coverage_by_asset[asset_id]
        if not coverage.scoreable:
            score.risk_flags.append("insufficient_factor_coverage")
        elif coverage.weighted_coverage < 0.72:
            score.risk_flags.append("degraded_factor_coverage")
        scores.append(score)
        if effects.risk_flags:
            risk_flags_by_asset[asset_id] = list(effects.risk_flags)
        if effects.confidence_penalty:
            confidence_penalty_by_asset[asset_id] = effects.confidence_penalty

    return ScoringStageResult(
        quality=quality,
        scores=scores,
        risk_flags_by_asset=risk_flags_by_asset,
        confidence_penalty_by_asset=confidence_penalty_by_asset,
    )


def explanation_stage(scores: list[AssetScore]) -> dict[str, AssetExplanation]:
    graph = build_explanation_graph()
    return {
        score.asset_id: graph.invoke({"asset_score": score})["explanation"]
        for score in scores
    }


def public_event_summary(events: list[StructuredEvent]) -> list[dict]:
    return [
        {
            "event_id": str(event.event_id),
            "title": event.canonical_title,
            "type": event.event_type,
            "strength": event.strength,
            "factor_ids": [mapping.factor_id for mapping in event.factors],
        }
        for event in events
    ]


def choose_analysis_status(*, publish_mode: PublishMode, quality: QualityGateResult) -> tuple[AnalysisStatus, bool]:
    should_publish = (
        publish_mode in {PublishMode.AUTO, PublishMode.EXPLICIT_PUBLISH}
        and quality.passed_for_publish
    )
    if should_publish:
        return AnalysisStatus.PUBLISHED, True
    if quality.passed_for_preview:
        return AnalysisStatus.PREVIEW_READY, False
    return AnalysisStatus.QUALITY_FAILED, False
