from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from widegold.repositories.factory import repository
from widegold.settings.config import asset_config
from widegold.schemas.query import AssetHistoryPoint, AssetHistoryResponse

router = APIRouter(tags=["assets"])


@router.get("/assets")
def list_assets():
    return asset_config()["assets"]


@router.get("/assets/{asset_id}/current")
def current_asset(asset_id: str):
    snapshot = repository().latest_published()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No published snapshot")
    for score in snapshot.assets:
        if score.asset_id == asset_id:
            return {
                "score": score,
                "explanation": snapshot.explanations.get(asset_id),
                "analysis_run_id": snapshot.analysis_run_id,
                "as_of": snapshot.as_of,
            }
    raise HTTPException(status_code=404, detail="Asset not found")


@router.get("/assets/{asset_id}/history", response_model=AssetHistoryResponse)
def asset_history(
    asset_id: str,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    limit: int = Query(default=120, ge=1, le=1000),
):
    known_assets = {row["id"] for row in asset_config()["assets"]}
    if asset_id not in known_assets:
        raise HTTPException(status_code=404, detail="Asset not found")
    end = end or date.today()
    start = start or (end - timedelta(days=180))
    if start > end:
        raise HTTPException(status_code=422, detail="start must be <= end")
    snapshots = repository().list_snapshots(start_date=start, end_date=end, published_only=True)
    # A manual Preview may later be explicitly published on the same business date. Public history
    # exposes one final point per date: the latest published snapshot for that analysis date.
    # list_snapshots orders by (analysis_date, as_of, published_at), so the last row seen for a
    # date is the one to keep; comparing only as_of left exact ties resolved by row order.
    latest_by_day = {}
    for snapshot in snapshots:
        latest_by_day[snapshot.analysis_date] = snapshot
    snapshots = [latest_by_day[key] for key in sorted(latest_by_day)]
    points: list[AssetHistoryPoint] = []
    for snapshot in snapshots:
        score = next((item for item in snapshot.assets if item.asset_id == asset_id), None)
        if score is None:
            continue
        points.append(AssetHistoryPoint(
            analysis_run_id=snapshot.analysis_run_id,
            analysis_date=snapshot.analysis_date,
            as_of=snapshot.as_of,
            score=score.score,
            label=score.label,
            confidence=score.confidence.confidence,
            tactical_score=score.tactical_score,
            swing_score=score.swing_score,
            strategic_score=score.strategic_score,
        ))
    return AssetHistoryResponse(asset_id=asset_id, points=points[-limit:])
