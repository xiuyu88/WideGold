from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from widegold.repositories.factory import repository
from widegold.schemas.common import AnalysisRunRequest
from widegold.services.analysis import run_analysis
from widegold.settings.app import get_settings


@dataclass(frozen=True)
class DispatchResult:
    analysis_run_id: UUID | None
    prefect_flow_run_id: UUID | None
    status: str


def dispatch_analysis(request: AnalysisRunRequest) -> DispatchResult:
    settings = get_settings()
    analysis_run_id = request.analysis_run_id or uuid4()
    request = request.model_copy(update={"analysis_run_id": analysis_run_id})
    repository().reserve_run(request)
    if settings.orchestration_mode.lower() != "prefect":
        snapshot = run_analysis(request)
        return DispatchResult(snapshot.analysis_run_id, None, snapshot.status)

    try:
        from prefect.deployments import run_deployment
    except ImportError as exc:  # pragma: no cover - production dependency path
        raise RuntimeError("Prefect orchestration requested but prefect is not installed") from exc

    flow_run = run_deployment(
        name=settings.prefect_deployment_name,
        parameters={"request_payload": request.model_dump(mode="json")},
        timeout=0,
    )
    return DispatchResult(analysis_run_id, flow_run.id, "PENDING")
