from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from apps.api.dependencies import admin_user
from widegold.auth.service import Principal
from widegold.repositories.factory import repository
from widegold.schemas.research import ResearchRequest, ResearchReviewCommand
from widegold.services.research import run_factor_research
from widegold.settings.app import get_settings

router = APIRouter(tags=["research"], dependencies=[Depends(admin_user)])


@router.post("/research", status_code=status.HTTP_202_ACCEPTED)
def create_research(request: ResearchRequest, user: Principal = Depends(admin_user)):
    request = request.model_copy(update={"requested_by": user.user_id})
    settings = get_settings()
    if settings.orchestration_mode == "prefect":
        try:
            from prefect.deployments import run_deployment
            flow_run = run_deployment(
                name=settings.prefect_research_deployment_name,
                parameters={"request_payload": request.model_dump(mode="json")},
                timeout=0,
            )
            repository().create_research_request(request, status="PENDING")
            repository().update_research_request(
                request.research_request_id,
                status="PENDING",
                prefect_flow_run_id=flow_run.id,
            )
            return {
                "research_request_id": str(request.research_request_id),
                "prefect_flow_run_id": str(flow_run.id),
                "status": "PENDING",
            }
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="Prefect is unavailable") from exc
    result = run_factor_research(request)
    return result.model_dump(mode="json")


@router.get("/research/{request_id}")
def get_research(request_id: UUID):
    row = repository().get_research_request(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Research request not found")
    return row


@router.post("/research/candidates/{candidate_id}/review")
def review_candidate(
    candidate_id: UUID,
    command: ResearchReviewCommand,
    user: Principal = Depends(admin_user),
):
    result = repository().review_factor_candidate(
        candidate_id,
        action=command.action,
        reviewer_id=user.user_id,
        comment=command.comment,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    repository().append_audit(
        actor_user_id=user.user_id,
        action=f"RESEARCH_{command.action.upper()}",
        resource_type="factor_candidate",
        resource_id=str(candidate_id),
        after=result,
    )
    return result
