from datetime import datetime
from uuid import UUID

from pydantic import Field

from widegold.domain.enums import DataStatus
from widegold.schemas.common import StrictModel


class FactorStateComponents(StrictModel):
    level: float | None = None
    trend: float | None = None
    surprise: float | None = None
    event: float | None = None


class FactorState(StrictModel):
    factor_id: str
    asset_id: str | None = None
    as_of_ts: datetime
    state: float = Field(ge=-100, le=100)
    components: FactorStateComponents
    reliability: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    status: DataStatus = DataStatus.VALID
    evidence_refs: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    event_ids: list[UUID] = Field(default_factory=list)
    logic_version: str = "1.0.0"
    data_vintage: str | None = "mock-1.0.0"
