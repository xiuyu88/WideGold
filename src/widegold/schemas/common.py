from datetime import date, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from widegold.domain.enums import AnalysisRunMode, PublishMode, TriggerType


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionSnapshot(StrictModel):
    factor_schema: str = "1.0.0"
    factor_logic: str = "1.0.0"
    weights: str = "1.0.0"
    event_rules: str = "1.0.0"
    prompts: str = "mock-1.0.0"
    scoring: str = "1.0.0"
    data_definition: str = "mock-1.0.0"
    model_routing: str = "1.0.0"
    indicator_registry: str = "1.0.0"
    factor_calculators: str = "1.0.0"
    news_collection: str = "1.0.0"
    assets: str = "1.0.0"
    resilience: str = "1.0.0"
    evaluation: str = "1.0.0"
    calendar: str = "1.0.0"


class AnalysisRunRequest(StrictModel):
    analysis_run_id: UUID | None = None
    analysis_date: date | None = None
    trigger_type: TriggerType = TriggerType.ADMIN_MANUAL
    run_mode: AnalysisRunMode = AnalysisRunMode.FULL_REFRESH
    publish_mode: PublishMode = PublishMode.PREVIEW_ONLY
    force_refresh: bool = False
    base_run_id: UUID | None = None
    selected_assets: list[str] | None = None
    selected_factors: list[str] | None = None
    requested_by: UUID | None = None


class AnalysisContext(StrictModel):
    analysis_run_id: UUID = Field(default_factory=uuid4)
    analysis_date: date
    as_of_ts: datetime
    data_cutoff_ts: datetime
    trigger_type: TriggerType
    run_mode: AnalysisRunMode
    publish_mode: PublishMode
    requested_by: UUID | None = None
    versions: VersionSnapshot = Field(default_factory=VersionSnapshot)
