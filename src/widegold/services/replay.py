from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from widegold.data.providers.replay import StoredPointInTimeProvider
from widegold.domain.enums import AnalysisRunMode, PublishMode, TriggerType
from widegold.repositories.factory import repository
from widegold.schemas.common import AnalysisRunRequest
from widegold.schemas.replay import ReplayDayResult, ReplayRequest, ReplayResult
from widegold.services.analysis import run_analysis
from widegold.services.trading_calendar import CNTradingCalendar

CN_TZ = ZoneInfo("Asia/Shanghai")


def run_replay(request: ReplayRequest, *, repo=None) -> ReplayResult:
    repo = repo or repository()
    calendar = CNTradingCalendar()
    day = request.start_date
    days: list[ReplayDayResult] = []

    from datetime import timedelta

    while day <= request.end_date:
        if not request.include_non_trading_days and not calendar.is_trading_day(day):
            days.append(ReplayDayResult(analysis_date=day, status="SKIPPED", warnings=["NOT_CN_TRADING_DAY"]))
            day += timedelta(days=1)
            continue
        cutoff = datetime.combine(day, time(18, 10), tzinfo=CN_TZ)
        try:
            snapshot = run_analysis(
                AnalysisRunRequest(
                    analysis_date=day,
                    trigger_type=TriggerType.REPLAY,
                    run_mode=AnalysisRunMode.REPLAY,
                    publish_mode=PublishMode.PREVIEW_ONLY,
                    force_refresh=False,
                    requested_by=request.requested_by,
                ),
                provider=StoredPointInTimeProvider(as_of=cutoff, repo=repo),
            )
            days.append(ReplayDayResult(
                analysis_date=day,
                analysis_run_id=snapshot.analysis_run_id,
                status=snapshot.status,
                published=False,
                warnings=snapshot.warnings,
            ))
        except Exception as exc:
            days.append(ReplayDayResult(
                analysis_date=day,
                status="FAILED",
                warnings=[f"{exc.__class__.__name__}:{exc}"],
            ))
        day += timedelta(days=1)

    completed = sum(row.status in {"PREVIEW_READY", "PUBLISHED"} for row in days)
    degraded = sum(row.status == "QUALITY_FAILED" for row in days)
    skipped = sum(row.status == "SKIPPED" for row in days)
    failed = sum(row.status == "FAILED" for row in days)
    return ReplayResult(
        start_date=request.start_date,
        end_date=request.end_date,
        days=days,
        completed=completed,
        degraded=degraded,
        skipped=skipped,
        failed=failed,
    )
