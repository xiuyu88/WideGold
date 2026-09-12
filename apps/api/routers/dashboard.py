from fastapi import APIRouter, HTTPException

from widegold.repositories.factory import repository
from widegold.runtime.cache import get_current_snapshot, set_current_snapshot
from widegold.schemas.scores import DashboardSnapshot

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/current", response_model=DashboardSnapshot)
def current_dashboard() -> DashboardSnapshot:
    cached = get_current_snapshot()
    if cached is not None:
        # Deliberately not re-written here: refreshing the TTL on every read let a cache entry
        # survive indefinitely under traffic, so a missed invalidation became permanent staleness.
        return cached
    snapshot = repository().latest_published()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No published snapshot is available yet.")
    set_current_snapshot(snapshot)
    return snapshot


@router.get("/dashboard/latest-preview", response_model=DashboardSnapshot)
def latest_preview_dashboard() -> DashboardSnapshot:
    # Never cache previews as the scheduled run can replace them without publishing.
    snapshot = repository().latest_preview()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No completed preview is available yet.")
    return snapshot
