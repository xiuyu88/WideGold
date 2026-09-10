from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from widegold.schemas.common import StrictModel
from widegold.schemas.factors import FactorState


class MachineAssetScore(StrictModel):
    asset_id: str
    asset_name: str
    score: float = Field(ge=0, le=100)
    direction_score: float = Field(ge=-100, le=100)
    label: str
    confidence: float = Field(ge=0, le=100)
    top_positive: list[str] = Field(default_factory=list)
    top_negative: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class MachineAnalysisEnvelope(StrictModel):
    """Stable machine-readable output contract for CLI/E2E consumers.

    This contract is intentionally separate from DashboardSnapshot so UI-oriented fields can
    evolve without silently breaking shell scripts, release gates, or external automation.
    """

    contract_version: str = "widegold.analysis.v1"
    analysis_run_id: UUID
    analysis_date: str
    as_of: datetime
    status: str
    published: bool
    assets: list[MachineAssetScore]
    factor_states: list[FactorState]
    factor_resolution: dict[str, Any] = Field(default_factory=dict)
    quality_gate: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
