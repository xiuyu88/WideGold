from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, status

from widegold.repositories.factory import repository
from widegold.schemas.indicators import IndicatorObservation
from widegold.services.indicator_ingest import ingest_indicator_observations
from widegold.settings.app import get_settings

router = APIRouter(tags=["machine-ingest"])


def _service_key(
    authorization: str | None = Header(default=None),
    x_widegold_ingest_key: str | None = Header(default=None),
) -> None:
    expected = get_settings().ingest_api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Machine indicator ingestion is not configured",
        )
    supplied = x_widegold_ingest_key
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ingest credential")


@router.post("/ingest/indicators")
def machine_ingest_indicators(
    items: list[IndicatorObservation],
    authorization: str | None = Header(default=None),
    x_widegold_ingest_key: str | None = Header(default=None),
):
    _service_key(authorization, x_widegold_ingest_key)
    try:
        result = ingest_indicator_observations(items)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repository().append_audit(
        actor_user_id=None,
        action="M2M_INGEST_INDICATORS",
        resource_type="indicator_observations",
        resource_id=",".join(result["indicator_ids"]),
        after={"accepted": result["accepted"]},
    )
    return result
