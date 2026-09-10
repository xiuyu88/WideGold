from datetime import datetime
from typing import Any

from pydantic import Field

from widegold.domain.enums import DataStatus
from widegold.schemas.common import StrictModel
from widegold.schemas.events import NewsDocument


class MockMarketBundle(StrictModel):
    analysis_date: str
    as_of: datetime
    base_factor_states: dict[str, float]
    news: list[NewsDocument]
    news_status: DataStatus = DataStatus.VALID
    news_warnings: list[str] = Field(default_factory=list)


class ProviderResult(StrictModel):
    provider: str
    source_id: str
    retrieved_at: datetime
    rows_count: int
    checksum: str | None = None
    cache_hit: bool = False
    fallback_used: bool = False
    status: DataStatus = DataStatus.VALID
    warnings: list[str] = Field(default_factory=list)
    data: dict[str, Any]
