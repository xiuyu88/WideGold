from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from widegold.domain.enums import DataStatus
from widegold.repositories.factory import repository
from widegold.schemas.factor_health import (
    FactorHealthItem,
    FactorHealthResponse,
    FactorHealthState,
    FactorHealthSummary,
)
from widegold.settings.config import factor_config


_STATUS_RANK = {
    DataStatus.VALID: 0,
    DataStatus.PARTIAL: 1,
    DataStatus.STALE: 2,
    DataStatus.CONFLICTED: 3,
    DataStatus.UNAVAILABLE: 4,
}


def _aggregate_status(states: list[FactorHealthState]) -> DataStatus:
    if not states:
        return DataStatus.UNAVAILABLE
    return max((item.status for item in states), key=lambda status: _STATUS_RANK.get(status, 99))


def _select_run(run_id: UUID | None) -> tuple[UUID, str, str | None, str | None]:
    repo = repository()
    if run_id is not None:
        run = repo.get_run_status(run_id)
        if run is None:
            raise LookupError(f"Analysis run {run_id} not found")
        return run_id, "explicit", run.get("analysis_date"), run.get("status")

    snapshot = repo.latest_published()
    if snapshot is not None:
        return snapshot.analysis_run_id, "latest_published", snapshot.analysis_date, snapshot.status

    for run in repo.list_runs(limit=20):
        candidate = UUID(str(run["analysis_run_id"]))
        if repo.get_factor_states(candidate):
            return candidate, "latest_available", run.get("analysis_date"), run.get("status")
    raise LookupError("No analysis run with factor states is available")


def factor_health(*, run_id: UUID | None = None) -> FactorHealthResponse:
    repo = repository()
    selected_run_id, source, analysis_date, run_status = _select_run(run_id)
    states = repo.get_factor_states(selected_run_id)
    if not states:
        raise LookupError(f"Analysis run {selected_run_id} has no factor states")

    definitions = {row["id"]: row for row in factor_config().get("factors", [])}
    grouped = defaultdict(list)
    for state in states:
        grouped[state.factor_id].append(FactorHealthState(
            factor_id=state.factor_id,
            asset_id=state.asset_id,
            as_of_ts=state.as_of_ts,
            state=state.state,
            reliability=state.reliability,
            coverage=state.coverage,
            status=state.status,
            quality_flags=list(state.quality_flags),
            evidence_count=len(state.evidence_refs),
            event_count=len(state.event_ids),
        ))

    items: list[FactorHealthItem] = []
    for factor_id, spec in sorted(definitions.items()):
        factor_states = sorted(grouped.get(factor_id, []), key=lambda x: x.asset_id or "")
        if not factor_states:
            factor_states = []
        quality_flags = sorted({flag for row in factor_states for flag in row.quality_flags})
        items.append(FactorHealthItem(
            factor_id=factor_id,
            name=str(spec.get("name", factor_id)),
            family=str(spec.get("family", "unknown")),
            scope=str(spec.get("scope", "global")),
            evidence=str(spec.get("evidence")) if spec.get("evidence") is not None else None,
            aggregate_status=_aggregate_status(factor_states),
            min_reliability=min((row.reliability for row in factor_states), default=0.0),
            min_coverage=min((row.coverage for row in factor_states), default=0.0),
            states=factor_states,
            quality_flags=quality_flags,
        ))

    counts = {status: 0 for status in DataStatus}
    for state in states:
        counts[state.status] = counts.get(state.status, 0) + 1
    avg_reliability = sum(row.reliability for row in states) / len(states)
    avg_coverage = sum(row.coverage for row in states) / len(states)

    return FactorHealthResponse(
        analysis_run_id=selected_run_id,
        analysis_date=analysis_date,
        status=run_status,
        source=source,
        summary=FactorHealthSummary(
            logical_factors=len(definitions),
            physical_states=len(states),
            valid=counts.get(DataStatus.VALID, 0),
            stale=counts.get(DataStatus.STALE, 0),
            partial=counts.get(DataStatus.PARTIAL, 0),
            unavailable=counts.get(DataStatus.UNAVAILABLE, 0),
            conflicted=counts.get(DataStatus.CONFLICTED, 0),
            average_reliability=avg_reliability,
            average_coverage=avg_coverage,
        ),
        factors=items,
    )
