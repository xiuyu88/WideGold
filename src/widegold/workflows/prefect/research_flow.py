from __future__ import annotations

from widegold.schemas.research import ResearchRequest
from widegold.services.research import run_factor_research

try:
    from prefect import flow, task
except ImportError:
    flow = task = None


def _run(request_payload: dict):
    request = ResearchRequest.model_validate(request_payload)
    return run_factor_research(request).model_dump(mode="json")


if flow is not None and task is not None:
    @task(name="widegold-factor-research-core", retries=1, retry_delay_seconds=10)
    def research_core_task(request_payload: dict):
        return _run(request_payload)

    @flow(name="widegold-factor-research", log_prints=True)
    def factor_research_flow(request_payload: dict):
        return research_core_task(request_payload)
else:
    def factor_research_flow(request_payload: dict):
        return _run(request_payload)
