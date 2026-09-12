from __future__ import annotations

from datetime import datetime
from threading import RLock
from uuid import UUID

from widegold.domain.enums import AnalysisStatus
from widegold.schemas.common import AnalysisContext
from widegold.schemas.events import StructuredEvent
from widegold.schemas.factors import FactorState
from widegold.schemas.indicators import IndicatorObservation
from widegold.schemas.resilience import FactorInput, QualityGateResult
from widegold.schemas.scores import DashboardSnapshot
from widegold.schemas.research import FactorResearchCandidate, ResearchEvidence, ResearchRequest
from widegold.schemas.trace import RunTraceEvent


class InMemorySnapshotRepository:
    """Development repository implementing the same persistence surface as PostgreSQL."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[UUID, DashboardSnapshot] = {}
        self._latest: UUID | None = None
        self._publish_order: dict[UUID, int] = {}
        self._publish_seq: int = 0
        self._states: dict[UUID, list[FactorState]] = {}
        self._runs: dict[UUID, dict] = {}
        self._run_events: dict[UUID, list[RunTraceEvent]] = {}
        self._trace_seq: int = 0
        self._audit: list[dict] = []
        self._research: dict[UUID, dict] = {}
        self._indicator_observations: dict[tuple[str, str | None], list[IndicatorObservation]] = {}
        self._provider_health: dict[str, dict] = {}
        self._documents: dict[UUID, object] = {}
        self._calibration_reports: dict[UUID, object] = {}
        self._config_versions: dict[tuple[str, str], dict] = {}




    def _ensure_config_versions(self) -> None:
        if self._config_versions:
            return
        from widegold.settings.config import load_bootstrap_yaml
        from widegold.settings.runtime_config import CONFIG_BINDINGS
        import hashlib, json

        for filename, binding in CONFIG_BINDINGS.items():
            content = load_bootstrap_yaml(filename)
            version = str(content.get("version", "1.0.0"))
            digest = hashlib.sha256(
                json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            self._config_versions[(binding.config_type, version)] = {
                "config_version_id": f"memory:{binding.config_type}:{version}",
                "config_type": binding.config_type,
                "version": version,
                "status": "ACTIVE",
                "content_hash": digest,
                "content": content,
                "effective_from": "memory",
                "effective_to": None,
                "created_by": None,
                "approved_by": None,
                "created_at": "memory",
                "approved_at": "memory",
            }

    def create_config_version(
        self, config_type: str, version: str, content: dict, *, status: str, actor_user_id: UUID | None
    ) -> tuple[dict, bool]:
        import hashlib
        import json

        self._ensure_config_versions()
        digest = hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        key = (config_type, version)
        existing = self._config_versions.get(key)
        if existing is not None:
            if existing["content_hash"] != digest:
                raise ValueError(f"Config {config_type} {version} already exists with different content")
            return dict(existing), False
        row = {
            "config_version_id": f"memory:{config_type}:{version}",
            "config_type": config_type,
            "version": version,
            "status": status,
            "content_hash": digest,
            "content": content,
            "effective_from": "memory",
            "effective_to": None,
            "created_by": str(actor_user_id) if actor_user_id else None,
            "approved_by": str(actor_user_id) if actor_user_id and status == "APPROVED" else None,
            "created_at": "memory",
            "approved_at": "memory" if status == "APPROVED" else None,
        }
        self._config_versions[key] = row
        return dict(row), True

    def list_config_versions(self, *, config_type: str | None = None) -> list[dict]:
        self._ensure_config_versions()
        rows = list(self._config_versions.values())
        if config_type is not None:
            rows = [row for row in rows if row["config_type"] == config_type]
        return [dict(row) for row in sorted(rows, key=lambda x: (x["config_type"], x["version"]))]

    def get_config_version(self, config_type: str, version: str) -> dict | None:
        self._ensure_config_versions()
        row = self._config_versions.get((config_type, version))
        return dict(row) if row is not None else None

    def activate_config_version(
        self, config_type: str, version: str, *, actor_user_id: UUID | None
    ) -> dict | None:
        self._ensure_config_versions()
        target = self._config_versions.get((config_type, version))
        if target is None:
            return None
        if target["status"] not in {"APPROVED", "ACTIVE"}:
            raise ValueError("Config must be APPROVED before activation")
        previous = None
        for (ctype, _), row in self._config_versions.items():
            if ctype == config_type and row["status"] == "ACTIVE":
                previous = {"version": row["version"], "status": row["status"]}
                if row is not target:
                    row["status"] = "APPROVED"
        target["status"] = "ACTIVE"
        target["approved_by"] = str(actor_user_id) if actor_user_id else target.get("approved_by")
        return {
            "config_type": config_type,
            "version": version,
            "status": "ACTIVE",
            "content_hash": target["content_hash"],
            "previous": previous,
            "effective_from": "memory",
        }

    def create_research_request(self, request: ResearchRequest, *, status: str = "RUNNING") -> None:
        self._research[request.research_request_id] = {
            "request": request, "status": status, "evidence": [], "candidates": []
        }

    def update_research_request(self, request_id: UUID, *, status: str, prefect_flow_run_id: UUID | None = None) -> None:
        if request_id in self._research:
            self._research[request_id]["status"] = status
            self._research[request_id]["prefect_flow_run_id"] = prefect_flow_run_id

    def save_research_evidence(self, items: list[ResearchEvidence]) -> None:
        for item in items:
            self._research.setdefault(item.research_request_id, {"evidence": [], "candidates": []})["evidence"].append(item)

    def save_factor_candidate(self, candidate: FactorResearchCandidate) -> None:
        self._research.setdefault(candidate.research_request_id, {"evidence": [], "candidates": []})["candidates"].append(candidate)

    def get_research_request(self, request_id: UUID) -> dict | None:
        row = self._research.get(request_id)
        if row is None:
            return None
        req = row.get("request")
        return {
            "research_request_id": str(request_id),
            "request_type": req.request_type if req else None,
            "target_factor_id": req.target_factor_id if req else None,
            "title": req.title if req else None,
            "question": req.question if req else None,
            "status": row.get("status"),
            "evidence": [e.model_dump(mode="json") for e in row.get("evidence", [])],
            "candidates": [c.model_dump(mode="json") for c in row.get("candidates", [])],
        }

    def review_factor_candidate(
        self, candidate_id: UUID, *, action: str, reviewer_id: UUID | None, comment: str | None
    ) -> dict | None:
        for row in self._research.values():
            for idx, candidate in enumerate(row.get("candidates", [])):
                if candidate.candidate_id == candidate_id:
                    status = {"approve_shadow":"SHADOW", "reject":"REJECTED", "revise":"REVISION_REQUIRED"}[action]
                    row["candidates"][idx] = candidate.model_copy(update={"status": status, "production_enabled": False})
                    return {"candidate_id": str(candidate_id), "status": status, "production_enabled": False}
        return None

    def append_audit(
        self,
        *,
        actor_user_id: UUID | None,
        action: str,
        resource_type: str,
        resource_id: str,
        before: dict | None = None,
        after: dict | None = None,
        request_id: str | None = None,
    ) -> None:
        self._audit.append({
            "actor_user_id": actor_user_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "before": before,
            "after": after,
            "request_id": request_id,
            "created_at": datetime.now().isoformat(),
        })

    def list_audit(self, *, limit: int = 100) -> list[dict]:
        rows = list(reversed(self._audit[-max(1, min(limit, 500)) :]))
        return [
            {
                **row,
                "actor_user_id": str(row.get("actor_user_id")) if row.get("actor_user_id") else None,
                "audit_id": f"memory:{idx}",
                "created_at": row.get("created_at") or "memory",
            }
            for idx, row in enumerate(rows, 1)
        ]

    def get_user_auth(self, username: str) -> dict | None:
        return None

    def reserve_run(self, request) -> None:
        from widegold.domain.enums import AnalysisStatus
        self._runs.setdefault(request.analysis_run_id, {"status": AnalysisStatus.PENDING, "request": request})

    def create_run(self, context: AnalysisContext) -> None:
        self._runs[context.analysis_run_id] = {"context": context, "status": AnalysisStatus.RUNNING}

    def list_runs(self, *, limit: int = 50) -> list[dict]:
        rows = []
        for run_id, row in self._runs.items():
            context = row.get("context")
            request = row.get("request")
            started_at = getattr(context, "as_of_ts", None)
            analysis_date = getattr(context, "analysis_date", None) or getattr(request, "analysis_date", None)
            status = row.get("status")
            rows.append({
                "analysis_run_id": str(run_id),
                "analysis_date": str(analysis_date) if analysis_date else None,
                "status": status.value if hasattr(status, "value") else str(status),
                "trigger_type": getattr(getattr(context, "trigger_type", None), "value", None) or getattr(getattr(request, "trigger_type", None), "value", None),
                "run_mode": getattr(getattr(context, "run_mode", None), "value", None) or getattr(getattr(request, "run_mode", None), "value", None),
                "publish_mode": getattr(getattr(context, "publish_mode", None), "value", None) or getattr(getattr(request, "publish_mode", None), "value", None),
                "started_at": started_at.isoformat() if started_at else None,
                "error_code": row.get("error_code"),
            })
        rows.sort(key=lambda x: x.get("started_at") or "", reverse=True)
        return rows[: max(1, min(limit, 200))]

    def get_run_status(self, run_id: UUID) -> dict | None:
        row = self._runs.get(run_id)
        if row is None:
            return None
        status = row.get("status")
        context = row.get("context")
        request = row.get("request")
        analysis_date = getattr(context, "analysis_date", None) or getattr(request, "analysis_date", None)
        as_of = getattr(context, "as_of_ts", None)
        return {
            "analysis_run_id": str(run_id),
            "analysis_date": str(analysis_date) if analysis_date else None,
            "as_of": as_of.isoformat() if as_of else None,
            "status": status.value if hasattr(status, "value") else str(status),
            "error_code": row.get("error_code"),
            "error_summary": row.get("error_summary"),
        }

    def update_run(self, run_id: UUID, status: AnalysisStatus, quality=None, error_code=None, error_summary=None) -> None:
        self._runs.setdefault(run_id, {})["status"] = status
        self._runs[run_id]["quality"] = quality
        self._runs[run_id]["error_code"] = error_code
        self._runs[run_id]["error_summary"] = error_summary


    def append_run_event(
        self,
        *,
        analysis_run_id: UUID,
        stage: str,
        event_type: str,
        status: str,
        level: str,
        message: str,
        progress: float | None,
        details: dict,
        created_at: datetime,
    ) -> RunTraceEvent:
        with self._lock:
            self._trace_seq += 1
            event = RunTraceEvent(
                trace_seq=self._trace_seq,
                analysis_run_id=analysis_run_id,
                stage=stage,
                event_type=event_type,
                status=status,
                level=level,
                message=message,
                progress=progress,
                details=details,
                created_at=created_at,
            )
            self._run_events.setdefault(analysis_run_id, []).append(event)
            return event

    def list_run_events(
        self, run_id: UUID, *, after_seq: int = 0, limit: int = 100
    ) -> list[RunTraceEvent]:
        return [e for e in self._run_events.get(run_id, []) if e.trace_seq > after_seq][:limit]

    def save_factor_inputs(self, run_id: UUID, inputs: list[FactorInput]) -> None:
        self._runs.setdefault(run_id, {})["factor_inputs"] = inputs

    def save_llm_runs(self, run_id: UUID, executions: list) -> None:
        self._runs.setdefault(run_id, {}).setdefault("llm_runs", []).extend(executions)

    def save_events(self, run_id: UUID, events: list[StructuredEvent]) -> None:
        self._runs.setdefault(run_id, {})["events"] = events


    def get_factor_inputs(self, run_id: UUID) -> list[FactorInput]:
        return list(self._runs.get(run_id, {}).get("factor_inputs", []))

    def get_events(self, run_id: UUID) -> list[StructuredEvent]:
        return list(self._runs.get(run_id, {}).get("events", []))

    def save_factor_states(self, run_id: UUID, states: list[FactorState]) -> None:
        self._states[run_id] = states

    def get_factor_states(self, run_id: UUID) -> list[FactorState]:
        return list(self._states.get(run_id, []))

    def latest_factor_states(
        self, before: datetime | None = None, *, lookback_hours: float | None = None
    ) -> dict[str, FactorState]:
        candidates: list[FactorState] = []
        for states in self._states.values():
            candidates.extend(s for s in states if before is None or s.as_of_ts < before)
        candidates.sort(key=lambda x: x.as_of_ts, reverse=True)
        result: dict[str, FactorState] = {}
        for item in candidates:
            result.setdefault(f"{item.factor_id}::{item.asset_id}" if item.asset_id else item.factor_id, item)
        return result


    def save_documents(self, documents) -> None:
        with self._lock:
            for document in documents:
                self._documents[document.document_id] = document

    def get_documents(self, document_ids) -> list:
        with self._lock:
            return [self._documents[item] for item in document_ids if item in self._documents]

    def get_documents_for_window(self, *, start: datetime, end: datetime, retrieved_before: datetime, limit: int = 100) -> list:
        with self._lock:
            rows = [
                doc for doc in self._documents.values()
                if start <= doc.published_at <= end and doc.retrieved_at <= retrieved_before
            ]
            rows.sort(key=lambda x: (x.published_at, x.retrieved_at), reverse=True)
            return rows[: max(1, limit)]

    def list_snapshots(self, *, start_date, end_date, published_only: bool = True) -> list[DashboardSnapshot]:
        rows = []
        for item in self._items.values():
            day = datetime.fromisoformat(item.analysis_date).date()
            if start_date <= day <= end_date and (not published_only or item.published):
                rows.append(item)
        # Matches the PostgreSQL ordering, including the publication-sequence tie-break, so both
        # backends give the public history the same "last row per day" answer.
        rows.sort(
            key=lambda x: (
                x.analysis_date,
                x.as_of,
                self._publish_order.get(x.analysis_run_id, 0),
            )
        )
        return rows

    def save_calibration_report(self, result, *, requested_by: UUID | None = None) -> None:
        self._calibration_reports[result.calibration_id] = result

    def get_calibration_report(self, calibration_id: UUID):
        return self._calibration_reports.get(calibration_id)

    def save_indicator_observations(self, observations: list[IndicatorObservation]) -> None:
        with self._lock:
            for item in observations:
                key = (item.indicator_id, item.asset_id)
                rows = self._indicator_observations.setdefault(key, [])
                same_date_source = [
                    r for r in rows
                    if r.observation_date == item.observation_date and r.source_id == item.source_id
                ]
                latest = max(same_date_source, key=lambda x: x.release_ts, default=None)
                if latest is not None and (
                    float(latest.value) == float(item.value)
                    and latest.definition_version == item.definition_version
                    and latest.status == item.status
                ):
                    continue
                rows.append(item)
                rows.sort(key=lambda x: (x.observation_date, x.release_ts))

    def get_indicator_history(
        self, indicator_id: str, *, as_of: datetime, asset_id: str | None = None, limit: int = 500
    ) -> list[IndicatorObservation]:
        with self._lock:
            rows = [r for r in self._indicator_observations.get((indicator_id, asset_id), []) if r.release_ts <= as_of]
            by_date = {}
            for row in sorted(rows, key=lambda x: (x.observation_date, x.release_ts), reverse=True):
                by_date.setdefault(row.observation_date, row)
            result = sorted(by_date.values(), key=lambda x: x.observation_date)
            return result[-limit:]

    def update_provider_health(self, provider_key: str, *, status: str, latency_ms: int | None, details: dict | None = None) -> None:
        self._provider_health[provider_key] = {"status": status, "latency_ms": latency_ms, "details": details or {}}

    def list_provider_health(self) -> list[dict]:
        return [{"provider_key": key, **value} for key, value in sorted(self._provider_health.items())]

    def save_snapshot(self, snapshot: DashboardSnapshot, quality: QualityGateResult | None = None) -> None:
        self.save(snapshot)

    def save(self, snapshot: DashboardSnapshot) -> None:
        with self._lock:
            self._items[snapshot.analysis_run_id] = snapshot
            if snapshot.published:
                self._publish_seq += 1
                self._publish_order[snapshot.analysis_run_id] = self._publish_seq
                self._latest = snapshot.analysis_run_id

    def get_snapshot(self, run_id: UUID) -> DashboardSnapshot | None:
        return self.get(run_id)

    def get(self, run_id: UUID) -> DashboardSnapshot | None:
        return self._items.get(run_id)

    def latest_published(self) -> DashboardSnapshot | None:
        # Mirrors the PostgreSQL repository: business ordering is the analysis date, and the
        # publication sequence only breaks ties within one day.
        published = [item for item in self._items.values() if item.published]
        if not published:
            return None
        return max(
            published,
            key=lambda item: (
                item.analysis_date or "",
                self._publish_order.get(item.analysis_run_id, 0),
            ),
        )

    def latest_preview(self) -> DashboardSnapshot | None:
        previews = [
            item for item in self._items.values()
            if not item.published and item.status in (
                AnalysisStatus.PREVIEW_READY.value, AnalysisStatus.QUALITY_FAILED.value
            )
        ]
        return max(previews, key=lambda item: (item.analysis_date, item.as_of), default=None)

    def latest_published_analysis_date(self):
        snapshot = self.latest_published()
        if snapshot is None or not snapshot.analysis_date:
            return None
        from datetime import date as _date

        return _date.fromisoformat(str(snapshot.analysis_date))

    def publish(self, run_id: UUID) -> DashboardSnapshot | None:
        snapshot = self._items.get(run_id)
        if snapshot is None:
            return None
        updated = snapshot.model_copy(update={"published": True, "status": AnalysisStatus.PUBLISHED.value})
        with self._lock:
            self._items[run_id] = updated
            self._publish_seq += 1
            self._publish_order[run_id] = self._publish_seq
            self._latest = run_id
        self.update_run(run_id, AnalysisStatus.PUBLISHED)
        return updated


snapshot_repository = InMemorySnapshotRepository()

# Test/development utility. Production repositories intentionally do not expose destructive reset.
def reset_memory_repository() -> None:
    snapshot_repository._items.clear()
    snapshot_repository._latest = None
    snapshot_repository._publish_order.clear()
    snapshot_repository._publish_seq = 0
    snapshot_repository._states.clear()
    snapshot_repository._runs.clear()
    snapshot_repository._run_events.clear()
    snapshot_repository._trace_seq = 0
    snapshot_repository._audit.clear()
    snapshot_repository._research.clear()
    snapshot_repository._indicator_observations.clear()
    snapshot_repository._provider_health.clear()
    snapshot_repository._documents.clear()
    snapshot_repository._calibration_reports.clear()
    snapshot_repository._config_versions.clear()
