from __future__ import annotations

import hmac
import os
from fastapi import Depends, FastAPI, Header, HTTPException

from widegold.external_bridge.adapters import BRIDGE_BUILD
from widegold.external_bridge.models import BridgeFetchRequest
from widegold.external_bridge.service import ExternalBridgeService

app = FastAPI(title="WideGold Free External Bridge", version="1.0.0")
service = ExternalBridgeService()


def _authorize(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("WIDEGOLD_EXTERNAL_INDICATOR_API_KEY", "")
    if not expected:
        return
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid bridge credential")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "widegold-free-external-bridge",
        "contract_version": "widegold.external-indicator.v1",
        "build": BRIDGE_BUILD,
        "capabilities": len(service.capabilities),
    }


@app.get("/capabilities")
def capabilities() -> dict:
    return {
        "contract_version": "widegold.external-indicator.v1",
        "items": service.capabilities,
    }


@app.post("/fetch", dependencies=[Depends(_authorize)])
def fetch(request: BridgeFetchRequest) -> dict:
    return service.fetch(request)
