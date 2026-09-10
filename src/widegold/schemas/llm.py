from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from widegold.domain.enums import ModelTier
from widegold.schemas.common import StrictModel


class ModelExecution(StrictModel):
    execution_id: UUID = Field(default_factory=uuid4)
    provider: str
    model_alias: str
    resolved_model: str
    tier: ModelTier
    task_type: str
    reasoning_effort: str
    started_at: datetime
    finished_at: datetime
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    schema_valid: bool
    retry_count: int = 0
    fallback_from: str | None = None
    status: str
    error_code: str | None = None
    error_message: str | None = None


class StructuredLLMResult(StrictModel):
    data: Any
    execution: ModelExecution
