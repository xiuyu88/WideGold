from fastapi import APIRouter

from widegold.schemas.scores import DashboardSnapshot
from widegold.services.mock_analysis import run_mock_analysis

router = APIRouter(tags=["mock"])


@router.post("/mock/run", response_model=DashboardSnapshot)
def run_mock() -> DashboardSnapshot:
    return run_mock_analysis(publish=True)
