from fastapi import FastAPI

from apps.api.routers import admin, assets, auth, dashboard, events, ingest, mock, research, scenarios
from widegold.settings.app import get_settings
from widegold.services.runtime_health import runtime_readiness

app = FastAPI(title="WideGold API", version="1.3.0")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(mock.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(assets.router, prefix="/api/v1")
app.include_router(events.router, prefix="/api/v1")
app.include_router(scenarios.router, prefix="/api/v1")
app.include_router(ingest.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1/admin")
app.include_router(research.router, prefix="/api/v1/admin")


@app.get("/health")
def health():
    settings = get_settings()
    return {
        "status": "ok",
        "service": "widegold-api",
        "version": "1.3.0",
        "persistence": settings.persistence,
        "orchestration": settings.orchestration_mode,
        "data_mode": settings.data_mode,
        "event_graph_mode": settings.event_graph_mode,
        "auth_mode": settings.auth_mode,
    }


@app.get("/ready")
def ready():
    return runtime_readiness().model_dump(mode="json")
