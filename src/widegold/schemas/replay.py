from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from widegold.schemas.common import StrictModel


class ReplayRequest(StrictModel):
    start_date: date
    end_date: date
    requested_by: UUID | None = None
    include_non_trading_days: bool = False

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days > 366:
            raise ValueError("V1 replay range is limited to 366 calendar days per run")
        return self


class ReplayDayResult(StrictModel):
    analysis_date: date
    analysis_run_id: UUID | None = None
    status: str
    published: bool = False
    warnings: list[str] = Field(default_factory=list)


class ReplayResult(StrictModel):
    start_date: date
    end_date: date
    days: list[ReplayDayResult]
    completed: int
    degraded: int
    skipped: int
    failed: int


class CalibrationRequest(StrictModel):
    calibration_id: UUID = Field(default_factory=uuid4)
    start_date: date
    end_date: date
    horizons: list[int] = Field(default_factory=lambda: [1, 5, 20])
    requested_by: UUID | None = None

    @model_validator(mode="after")
    def validate_request(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if not self.horizons:
            raise ValueError("at least one horizon is required")
        clean = sorted(set(int(x) for x in self.horizons))
        if any(x < 1 or x > 120 for x in clean):
            raise ValueError("calibration horizons must be between 1 and 120 trading observations")
        self.horizons = clean
        return self


class CalibrationMetric(StrictModel):
    asset_id: str
    horizon: int
    samples: int
    hit_rate: float | None = None
    mean_forward_return: float | None = None
    signal_return_spearman: float | None = None


class FactorCalibrationMetric(StrictModel):
    factor_id: str
    horizon: int
    samples: int
    contribution_return_spearman: float | None = None


class ScoreBucketMetric(StrictModel):
    bucket: str
    horizon: int
    samples: int
    mean_forward_return: float | None = None


class CalibrationResult(StrictModel):
    calibration_id: UUID
    start_date: date
    end_date: date
    created_at: datetime
    horizons: list[int]
    evaluation_version: str = "1.0.0"
    published_runs: int
    evaluated_samples: int
    asset_metrics: list[CalibrationMetric]
    factor_metrics: list[FactorCalibrationMetric]
    score_buckets: list[ScoreBucketMetric]
    rank_ic_by_horizon: dict[int, float | None]
    warnings: list[str] = Field(default_factory=list)
