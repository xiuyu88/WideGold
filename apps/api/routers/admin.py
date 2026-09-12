import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from apps.api.dependencies import admin_user
from widegold.auth.service import Principal
from widegold.domain.enums import AnalysisRunMode, PublishMode, TriggerType
from widegold.repositories.factory import repository
from widegold.runtime.cache import invalidate_current_snapshot
from widegold.schemas.common import AnalysisRunRequest
from widegold.schemas.indicators import IndicatorObservation
from widegold.schemas.replay import CalibrationRequest, ReplayRequest
from widegold.services.indicator_ingest import ingest_indicator_observations
from widegold.services.calibration import run_calibration
from widegold.services.replay import run_replay
from widegold.schemas.trace import RunTracePage
from widegold.schemas.system import ConfigActivationCommand, ConfigVersionStageCommand
from widegold.services.dispatcher import dispatch_analysis
from widegold.services.config_validation import validate_runtime_config
from widegold.services.runtime_health import runtime_readiness
from widegold.services.external_indicator_diagnostics import external_indicator_diagnostics
from widegold.services.factor_health import factor_health
from widegold.services.runtime_config_admin import (
    activate_runtime_config_version,
    get_runtime_config_version,
    list_runtime_config_versions,
    stage_runtime_config_version,
)
from widegold.settings.app import get_settings

router = APIRouter(tags=["admin"], dependencies=[Depends(admin_user)])

TERMINAL_STATUSES = {
    "DATA_READY",
    "QUALITY_FAILED",
    "PREVIEW_READY",
    "PUBLISHED",
    "FAILED",
    "CANCELLED",
    "SKIPPED",
}


class AdminAnalysisCommand(BaseModel):
    run_mode: AnalysisRunMode = AnalysisRunMode.FULL_REFRESH
    publish_mode: PublishMode = PublishMode.PREVIEW_ONLY
    force_refresh: bool = True
    base_run_id: UUID | None = None


@router.post("/analysis/run", status_code=status.HTTP_202_ACCEPTED)
def trigger_analysis(command: AdminAnalysisCommand, user: Principal = Depends(admin_user)):
    if command.run_mode == AnalysisRunMode.REANALYZE and command.base_run_id is None:
        raise HTTPException(status_code=422, detail="REANALYZE requires base_run_id")
    request = AnalysisRunRequest(
        trigger_type=TriggerType.ADMIN_MANUAL,
        run_mode=command.run_mode,
        publish_mode=PublishMode.PREVIEW_ONLY,
        force_refresh=command.force_refresh,
        base_run_id=command.base_run_id,
        requested_by=user.user_id,
    )
    result = dispatch_analysis(request)
    repository().append_audit(
        actor_user_id=user.user_id, action="ADMIN_RUN_ANALYSIS", resource_type="analysis_run",
        resource_id=str(result.analysis_run_id), after={"run_mode": command.run_mode.value}
    )
    return {
        "analysis_run_id": str(result.analysis_run_id),
        "prefect_flow_run_id": str(result.prefect_flow_run_id) if result.prefect_flow_run_id else None,
        "status": result.status,
    }


@router.post("/data/update", status_code=status.HTTP_202_ACCEPTED)
def update_data(user: Principal = Depends(admin_user)):
    result = dispatch_analysis(AnalysisRunRequest(
        trigger_type=TriggerType.ADMIN_MANUAL,
        run_mode=AnalysisRunMode.DATA_ONLY,
        publish_mode=PublishMode.PREVIEW_ONLY,
        force_refresh=True,
        requested_by=user.user_id,
    ))
    repository().append_audit(
        actor_user_id=user.user_id, action="ADMIN_UPDATE_DATA", resource_type="analysis_run",
        resource_id=str(result.analysis_run_id)
    )
    return {
        "analysis_run_id": str(result.analysis_run_id),
        "prefect_flow_run_id": str(result.prefect_flow_run_id) if result.prefect_flow_run_id else None,
        "status": result.status,
    }


@router.get("/runs")
def list_runs(limit: int = Query(default=50, ge=1, le=200)):
    return {"items": repository().list_runs(limit=limit)}


@router.get("/runs/{run_id}")
def get_run(run_id: UUID):
    repo = repository()
    run = repo.get_run_status(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    snapshot = repo.get_snapshot(run_id)
    return {**run, "snapshot": snapshot.model_dump(mode="json") if snapshot else None}


@router.get("/runs/{run_id}/trace", response_model=RunTracePage)
def get_run_trace(
    run_id: UUID,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
):
    repo = repository()
    run = repo.get_run_status(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    events = repo.list_run_events(run_id, after_seq=after_seq, limit=limit)
    next_seq = events[-1].trace_seq if events else after_seq
    return RunTracePage(
        analysis_run_id=run_id,
        events=events,
        next_seq=next_seq,
        terminal=run["status"] in TERMINAL_STATUSES,
    )


@router.get("/runs/{run_id}/trace/stream")
async def stream_run_trace(run_id: UUID, after_seq: int = Query(default=0, ge=0)):
    """Stream *coarse business trace events* only.

    Full Docker stdout, Prefect internals, prompts and token-level model streams are intentionally
    excluded. This keeps the admin channel cheap and bounded.
    """
    settings = get_settings()
    if not settings.trace_frontend_enabled:
        raise HTTPException(status_code=404, detail="Frontend trace streaming is disabled")
    repo = repository()
    if repo.get_run_status(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_stream():
        cursor = after_seq
        idle_cycles = 0
        while True:
            events = repo.list_run_events(run_id, after_seq=cursor, limit=settings.trace_page_size)
            if events:
                idle_cycles = 0
                for event in events:
                    cursor = event.trace_seq
                    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                    yield f"id: {event.trace_seq}\nevent: trace\ndata: {payload}\n\n"
            else:
                idle_cycles += 1

            run = repo.get_run_status(run_id)
            if run is None:
                yield "event: error\ndata: {\"message\":\"run disappeared\"}\n\n"
                return
            if run["status"] in TERMINAL_STATUSES and not events:
                payload = json.dumps({"status": run["status"], "cursor": cursor})
                yield f"event: done\ndata: {payload}\n\n"
                return
            if idle_cycles % 15 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(max(0.25, settings.trace_sse_poll_seconds))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/publish")
def publish_run(
    run_id: UUID,
    allow_backdated: bool = Query(default=False),
    user: Principal = Depends(admin_user),
):
    repo = repository()
    snapshot = repo.get_snapshot(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if snapshot.status not in {"PREVIEW_READY", "PUBLISHED"}:
        raise HTTPException(status_code=409, detail="Run is not publishable")
    if not snapshot.quality_gate.get("passed_for_publish", False):
        raise HTTPException(status_code=409, detail="Quality gate does not allow publication")
    # Publishing a replay or an older preview would otherwise silently replace the dashboard with
    # a stale analysis. It stays possible, but only as an explicit decision.
    current_date = getattr(repo, "latest_published_analysis_date", lambda: None)()
    if (
        not allow_backdated
        and current_date is not None
        and snapshot.analysis_date
        and str(snapshot.analysis_date) < str(current_date)
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run analysis date {snapshot.analysis_date} is older than the currently published "
                f"{current_date}; pass allow_backdated=true to publish it deliberately."
            ),
        )
    published = repo.publish(run_id)
    if published is not None:
        # Invalidate rather than cache this snapshot directly: with allow_backdated the published
        # run is not necessarily the one the dashboard should serve, and latest_published() is the
        # single place that decides.
        invalidate_current_snapshot()
        repo.append_audit(
            actor_user_id=user.user_id, action="ADMIN_PUBLISH", resource_type="analysis_run",
            resource_id=str(run_id), after={"status": "PUBLISHED"}
        )
    return published


@router.post("/data/indicators")
def ingest_indicators(items: list[IndicatorObservation], user: Principal = Depends(admin_user)):
    try:
        result = ingest_indicator_observations(items)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repository().append_audit(
        actor_user_id=user.user_id, action="ADMIN_INGEST_INDICATORS", resource_type="indicator_observations",
        resource_id=",".join(result["indicator_ids"]), after={"accepted": result["accepted"]}
    )
    return result


@router.get("/providers/health")
def provider_health():
    return {"providers": repository().list_provider_health()}

@router.get("/audit")
def audit_log(limit: int = Query(default=100, ge=1, le=500)):
    return {"items": repository().list_audit(limit=limit)}



@router.post("/replay", status_code=status.HTTP_202_ACCEPTED)
def replay_history(request: ReplayRequest, user: Principal = Depends(admin_user)):
    request = request.model_copy(update={"requested_by": user.user_id})
    settings = get_settings()
    if settings.orchestration_mode.lower() == "prefect":
        try:
            from prefect.deployments import run_deployment
            flow_run = run_deployment(
                name=settings.prefect_replay_deployment_name,
                parameters={"request_payload": request.model_dump(mode="json")},
                timeout=0,
            )
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="Prefect is unavailable") from exc
        repository().append_audit(
            actor_user_id=user.user_id, action="ADMIN_REPLAY", resource_type="replay",
            resource_id=str(flow_run.id), after=request.model_dump(mode="json")
        )
        return {"prefect_flow_run_id": str(flow_run.id), "status": "PENDING"}
    result = run_replay(request)
    repository().append_audit(
        actor_user_id=user.user_id, action="ADMIN_REPLAY", resource_type="replay",
        resource_id=f"{request.start_date}:{request.end_date}", after=result.model_dump(mode="json")
    )
    return result.model_dump(mode="json")


@router.post("/calibration", status_code=status.HTTP_202_ACCEPTED)
def run_calibration_review(request: CalibrationRequest, user: Principal = Depends(admin_user)):
    request = request.model_copy(update={"requested_by": user.user_id})
    settings = get_settings()
    if settings.orchestration_mode.lower() == "prefect":
        try:
            from prefect.deployments import run_deployment
            flow_run = run_deployment(
                name=settings.prefect_calibration_deployment_name,
                parameters={"request_payload": request.model_dump(mode="json")},
                timeout=0,
            )
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="Prefect is unavailable") from exc
        repository().append_audit(
            actor_user_id=user.user_id, action="ADMIN_CALIBRATION", resource_type="calibration",
            resource_id=str(request.calibration_id), after={"prefect_flow_run_id": str(flow_run.id)}
        )
        return {
            "calibration_id": str(request.calibration_id),
            "prefect_flow_run_id": str(flow_run.id),
            "status": "PENDING",
        }
    result = run_calibration(request)
    repository().append_audit(
        actor_user_id=user.user_id, action="ADMIN_CALIBRATION", resource_type="calibration",
        resource_id=str(result.calibration_id), after={"status": "COMPLETED"}
    )
    return result.model_dump(mode="json")


@router.get("/calibration/{calibration_id}")
def get_calibration(calibration_id: UUID):
    result = repository().get_calibration_report(calibration_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Calibration report not found")
    return result.model_dump(mode="json")


@router.post("/config/versions", status_code=status.HTTP_201_CREATED)
def stage_config_version(command: ConfigVersionStageCommand, user: Principal = Depends(admin_user)):
    try:
        result = stage_runtime_config_version(
            command.config_type,
            command.version,
            command.content,
            status=command.status,
            actor_user_id=user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repository().append_audit(
        actor_user_id=user.user_id,
        action="ADMIN_STAGE_CONFIG",
        resource_type="config_version",
        resource_id=f"{command.config_type.upper()}:{command.version}",
        after={
            "status": result["status"],
            "created": result["created"],
            "content_hash": result["content_hash"],
            "validation_valid": result["validation"]["valid"],
        },
    )
    return result


@router.get("/config/versions")
def config_versions(config_type: str | None = Query(default=None)):
    return {"items": list_runtime_config_versions(config_type)}


@router.get("/config/versions/{config_type}/{version}")
def config_version_detail(config_type: str, version: str):
    item = get_runtime_config_version(config_type, version)
    if item is None:
        raise HTTPException(status_code=404, detail="Config version not found")
    return item


@router.post("/config/activate")
def activate_config(command: ConfigActivationCommand, user: Principal = Depends(admin_user)):
    repo = repository()
    before = [
        row for row in repo.list_config_versions(config_type=command.config_type.upper())
        if row.get("status") == "ACTIVE"
    ]
    try:
        result = activate_runtime_config_version(
            command.config_type, command.version, actor_user_id=user.user_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repo.append_audit(
        actor_user_id=user.user_id,
        action="ADMIN_ACTIVATE_CONFIG",
        resource_type="config_version",
        resource_id=f"{command.config_type.upper()}:{command.version}",
        before={"active": before},
        after=result,
    )
    return result


@router.get("/factors/health")
def factor_health_view(run_id: UUID | None = Query(default=None)):
    try:
        return factor_health(run_id=run_id).model_dump(mode="json")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/system/diagnostics")
def system_diagnostics():
    repo = repository()
    snapshot = repo.latest_published()
    readiness = runtime_readiness()
    external = external_indicator_diagnostics()
    external_summary = {
        "total": len(external),
        "available": sum(1 for item in external if item["operational_status"] == "AVAILABLE"),
        "stale": sum(1 for item in external if item["operational_status"] == "STALE"),
        "missing": sum(1 for item in external if item["operational_status"] == "MISSING"),
    }
    return {
        "config": readiness.config.model_dump(mode="json"),
        "readiness": readiness.model_dump(mode="json"),
        "external_bridge_configured": bool(get_settings().external_indicator_url),
        "external_summary": external_summary,
        "external_indicators": external,
        "providers": repo.list_provider_health(),
        "recent_runs": repo.list_runs(limit=10),
        "latest_published_run_id": str(snapshot.analysis_run_id) if snapshot else None,
        "latest_analysis_date": snapshot.analysis_date if snapshot else None,
        "latest_quality_gate": snapshot.quality_gate if snapshot else None,
    }
