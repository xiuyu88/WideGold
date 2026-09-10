from uuid import UUID, uuid4

from pydantic import Field

from widegold.schemas.common import StrictModel


class ScenarioRequest(StrictModel):
    assumption: str
    base_analysis_run_id: UUID | None = None
    selected_assets: list[str] | None = None


class FactorShock(StrictModel):
    factor_id: str
    delta: float = Field(ge=-100, le=100)
    rationale: str


class ScenarioAssetImpact(StrictModel):
    asset_id: str
    base_score: float
    scenario_score: float
    delta: float


class ScenarioResult(StrictModel):
    scenario_id: UUID = Field(default_factory=uuid4)
    assumption: str
    factor_shocks: list[FactorShock]
    asset_impacts: list[ScenarioAssetImpact]
    conditional_branches: list[str] = Field(default_factory=list)
    explanation: str
    is_hypothetical: bool = True
