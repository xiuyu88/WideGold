from datetime import datetime
from uuid import UUID

from pydantic import Field

from widegold.schemas.common import StrictModel


class RunTraceEvent(StrictModel):
    trace_seq: int
    analysis_run_id: UUID
    stage: str
    event_type: str
    status: str
    level: str
    message: str
    progress: float | None = Field(default=None, ge=0, le=1)
    details: dict = Field(default_factory=dict)
    created_at: datetime


class RunTracePage(StrictModel):
    analysis_run_id: UUID
    events: list[RunTraceEvent]
    next_seq: int
    terminal: bool = False
