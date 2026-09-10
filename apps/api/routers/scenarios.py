from fastapi import APIRouter, HTTPException

from widegold.graphs.scenario import run_scenario
from widegold.schemas.scenario import ScenarioRequest, ScenarioResult

router = APIRouter(tags=["scenarios"])


@router.post("/scenarios", response_model=ScenarioResult)
def scenario(request: ScenarioRequest):
    try:
        return run_scenario(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
