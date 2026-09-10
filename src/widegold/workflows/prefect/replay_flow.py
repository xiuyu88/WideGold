from __future__ import annotations

from widegold.schemas.replay import CalibrationRequest, ReplayRequest
from widegold.services.calibration import run_calibration
from widegold.services.replay import run_replay

try:
    from prefect import flow, task
except ImportError:
    flow = task = None


def _run_replay(payload: dict):
    return run_replay(ReplayRequest.model_validate(payload)).model_dump(mode="json")


def _run_calibration(payload: dict):
    return run_calibration(CalibrationRequest.model_validate(payload)).model_dump(mode="json")


if flow is not None and task is not None:
    @task(name="widegold-point-in-time-replay", retries=0)
    def replay_task(payload: dict):
        return _run_replay(payload)

    @flow(name="widegold-replay-backfill", log_prints=True, retries=0)
    def replay_backfill_flow(request_payload: dict):
        return replay_task(request_payload)

    @task(name="widegold-calibration-review", retries=0)
    def calibration_task(payload: dict):
        return _run_calibration(payload)

    @flow(name="widegold-calibration-review", log_prints=True, retries=0)
    def calibration_review_flow(request_payload: dict):
        return calibration_task(request_payload)
else:
    def replay_backfill_flow(request_payload: dict):
        return _run_replay(request_payload)

    def calibration_review_flow(request_payload: dict):
        return _run_calibration(request_payload)
