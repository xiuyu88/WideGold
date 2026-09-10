from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from widegold.observability.logging import get_logger
from widegold.repositories.factory import repository
from widegold.schemas.trace import RunTraceEvent

logger = get_logger("widegold.trace")


@dataclass
class RunTracer:
    analysis_run_id: UUID

    def emit(
        self,
        stage: str,
        status: str,
        message: str,
        *,
        progress: float | None = None,
        level: str = "INFO",
        event_type: str = "STAGE",
        details: dict | None = None,
    ) -> RunTraceEvent | None:
        """Write a coarse business trace event.

        Trace persistence is deliberately best-effort: observability must never become a
        single point of failure for the investment analysis itself.
        """
        details = details or {}
        log_level = getattr(logger, level.upper(), logger.info)
        log_level(
            message,
            extra={
                "analysis_run_id": self.analysis_run_id,
                "stage": stage,
                "details": details,
            },
        )
        try:
            return repository().append_run_event(
                analysis_run_id=self.analysis_run_id,
                stage=stage,
                event_type=event_type,
                status=status,
                level=level.upper(),
                message=message,
                progress=progress,
                details=details,
                created_at=datetime.now(timezone.utc),
            )
        except Exception:
            logger.warning(
                "run trace persistence failed; analysis will continue",
                exc_info=True,
                extra={"analysis_run_id": self.analysis_run_id, "stage": stage},
            )
            return None
