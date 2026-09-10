from __future__ import annotations

from widegold.schemas.factors import FactorState
from widegold.schemas.machine import MachineAnalysisEnvelope, MachineAssetScore
from widegold.schemas.scores import DashboardSnapshot


def build_machine_analysis_envelope(
    snapshot: DashboardSnapshot,
    *,
    factor_states: list[FactorState],
) -> MachineAnalysisEnvelope:
    """Convert an internal dashboard snapshot into the stable CLI contract."""
    assets = [
        MachineAssetScore(
            asset_id=item.asset_id,
            asset_name=item.asset_name,
            score=item.score,
            direction_score=item.direction_score,
            label=item.label,
            confidence=item.confidence.confidence,
            top_positive=item.top_positive,
            top_negative=item.top_negative,
            risk_flags=item.risk_flags,
        )
        for item in snapshot.assets
    ]
    return MachineAnalysisEnvelope(
        analysis_run_id=snapshot.analysis_run_id,
        analysis_date=snapshot.analysis_date,
        as_of=snapshot.as_of,
        status=snapshot.status,
        published=snapshot.published,
        assets=assets,
        factor_states=factor_states,
        factor_resolution=snapshot.factor_resolution,
        quality_gate=snapshot.quality_gate,
        events=snapshot.top_events,
        warnings=snapshot.warnings,
    )
