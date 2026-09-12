from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
from uuid import UUID

from sqlalchemy import delete, desc, select, text

from widegold.db.models import (
    AnalysisRunModel,
    AnalysisSnapshotModel,
    CalibrationReportModel,
    ConfigVersionModel,
    AuditLogModel,
    AssetScoreModel,
    EventFactorLinkModel,
    EventModel,
    FactorInputModel,
    IndicatorDefinitionModel,
    IndicatorObservationModel,
    ProviderHealthModel,
    FactorCandidateModel,
    ApprovalRecordModel,
    ResearchEvidenceModel,
    ResearchRequestModel,
    RawDocumentModel,
    LLMRunModel,
    RunEventModel,
    UserModel,
    UserRoleModel,
    FactorStateModel,
    ScoreContributionModel,
)
from widegold.db.session import db_session
from widegold.domain.enums import AnalysisStatus, SourceTier
from widegold.schemas.common import AnalysisContext
from widegold.schemas.events import NewsDocument, StructuredEvent
from widegold.schemas.factors import FactorState
from widegold.schemas.indicators import IndicatorObservation
from widegold.schemas.resilience import FactorInput, QualityGateResult
from widegold.schemas.research import FactorResearchCandidate, ResearchEvidence, ResearchRequest
from widegold.schemas.scores import DashboardSnapshot
from widegold.schemas.trace import RunTraceEvent
from widegold.schemas.replay import CalibrationResult


# Widest max_stale_hours in factor_runtime (quarterly = 3600h) plus headroom. Anything older can
# never survive the staleness check in factor_resolution, so it does not belong in the scan.
_LKG_SCAN_LOOKBACK_HOURS = 4320.0


class PostgresRepository:
    """PostgreSQL persistence facade for the V1 modular monolith."""




    def create_config_version(
        self,
        config_type: str,
        version: str,
        content: dict,
        *,
        status: str,
        actor_user_id: UUID | None,
    ) -> tuple[dict, bool]:
        import json

        digest = hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        now = datetime.now(timezone.utc)
        with db_session() as session:
            existing = session.execute(
                select(ConfigVersionModel).where(
                    ConfigVersionModel.config_type == config_type,
                    ConfigVersionModel.version == version,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.content_hash != digest:
                    raise ValueError(
                        f"Config {config_type} {version} already exists with different content"
                    )
                return self._config_version_dict(existing, include_content=True), False
            row = ConfigVersionModel(
                config_type=config_type,
                version=version,
                status=status,
                content_hash=digest,
                content_json=content,
                effective_from=now,
                effective_to=None,
                created_by=actor_user_id,
                approved_by=actor_user_id if status == "APPROVED" else None,
                created_at=now,
                approved_at=now if status == "APPROVED" else None,
            )
            session.add(row)
            session.flush()
            return self._config_version_dict(row, include_content=True), True

    @staticmethod
    def _config_version_dict(row: ConfigVersionModel, *, include_content: bool = False) -> dict:
        result = {
            "config_version_id": str(row.config_version_id),
            "config_type": row.config_type,
            "version": row.version,
            "status": row.status,
            "content_hash": row.content_hash,
            "effective_from": row.effective_from.isoformat(),
            "effective_to": row.effective_to.isoformat() if row.effective_to else None,
            "created_by": str(row.created_by) if row.created_by else None,
            "approved_by": str(row.approved_by) if row.approved_by else None,
            "created_at": row.created_at.isoformat(),
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        }
        if include_content:
            result["content"] = row.content_json
        return result

    def list_config_versions(self, *, config_type: str | None = None) -> list[dict]:
        with db_session() as session:
            stmt = select(ConfigVersionModel)
            if config_type is not None:
                stmt = stmt.where(ConfigVersionModel.config_type == config_type)
            rows = session.execute(
                stmt.order_by(ConfigVersionModel.config_type, desc(ConfigVersionModel.created_at))
            ).scalars().all()
            return [
                {
                    "config_version_id": str(row.config_version_id),
                    "config_type": row.config_type,
                    "version": row.version,
                    "status": row.status,
                    "content_hash": row.content_hash,
                    "effective_from": row.effective_from.isoformat(),
                    "effective_to": row.effective_to.isoformat() if row.effective_to else None,
                    "created_by": str(row.created_by) if row.created_by else None,
                    "approved_by": str(row.approved_by) if row.approved_by else None,
                    "created_at": row.created_at.isoformat(),
                    "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                }
                for row in rows
            ]

    def get_config_version(self, config_type: str, version: str) -> dict | None:
        with db_session() as session:
            row = session.execute(
                select(ConfigVersionModel).where(
                    ConfigVersionModel.config_type == config_type,
                    ConfigVersionModel.version == version,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "config_version_id": str(row.config_version_id),
                "config_type": row.config_type,
                "version": row.version,
                "status": row.status,
                "content_hash": row.content_hash,
                "content": row.content_json,
                "effective_from": row.effective_from.isoformat(),
                "effective_to": row.effective_to.isoformat() if row.effective_to else None,
                "created_by": str(row.created_by) if row.created_by else None,
                "approved_by": str(row.approved_by) if row.approved_by else None,
                "created_at": row.created_at.isoformat(),
                "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            }

    def activate_config_version(
        self, config_type: str, version: str, *, actor_user_id: UUID | None
    ) -> dict | None:
        """Atomically validate, activate and materialize one runtime config version.

        A transaction-level advisory lock serializes config activations across API processes.
        If prospective cross-config validation or reference materialization fails, ``db_session``
        rolls the ACTIVE switch back together with every materialized change.
        """
        from widegold.db.reference_materializer import (
            ensure_weight_materialization,
            materialize_reference_tables,
        )
        from widegold.services.config_validation import validate_runtime_config_bundle
        from widegold.settings.runtime_config import CONFIG_BINDINGS

        now = datetime.now(timezone.utc)
        with db_session() as session:
            # One fixed application-level key is sufficient because config activations are rare,
            # administrative operations and need a globally consistent ACTIVE bundle.
            session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": 937_401_101},
            )
            target = session.execute(
                select(ConfigVersionModel)
                .where(
                    ConfigVersionModel.config_type == config_type,
                    ConfigVersionModel.version == version,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if target is None:
                return None
            if target.status not in {"APPROVED", "ACTIVE"}:
                raise ValueError(
                    f"Config {config_type} {version} must be APPROVED before activation; "
                    f"current status={target.status}"
                )

            current = session.execute(
                select(ConfigVersionModel)
                .where(
                    ConfigVersionModel.config_type == config_type,
                    ConfigVersionModel.status == "ACTIVE",
                )
                .with_for_update()
            ).scalar_one_or_none()
            previous = None
            if current is not None:
                previous = {
                    "version": current.version,
                    "status": current.status,
                    "effective_from": current.effective_from.isoformat(),
                }

            if current is not None and current.config_version_id != target.config_version_id:
                current.status = "APPROVED"
                current.effective_to = now
                session.flush()

            target.status = "ACTIVE"
            target.effective_from = now
            target.effective_to = None
            target.approved_by = actor_user_id or target.approved_by
            target.approved_at = target.approved_at or now
            session.flush()

            active_rows = session.execute(
                select(ConfigVersionModel).where(ConfigVersionModel.status == "ACTIVE")
            ).scalars().all()
            active_by_type = {row.config_type: dict(row.content_json) for row in active_rows}
            filename_by_type = {
                binding.config_type: filename for filename, binding in CONFIG_BINDINGS.items()
            }
            missing = sorted(set(filename_by_type) - set(active_by_type))
            if missing:
                raise ValueError(
                    "Cannot activate an incomplete runtime config bundle; missing ACTIVE types: "
                    + ", ".join(missing)
                )
            prospective_bundle = {
                filename_by_type[config_type_]: content
                for config_type_, content in active_by_type.items()
                if config_type_ in filename_by_type
            }
            validation = validate_runtime_config_bundle(prospective_bundle)
            if not validation.valid:
                errors = [
                    f"{issue.code}:{issue.resource or '-'}"
                    for issue in validation.issues
                    if issue.level == "ERROR"
                ]
                raise ValueError(
                    "Runtime config activation rejected by cross-config validation: "
                    + ", ".join(errors[:12])
                )

            materialize_reference_tables(session, active_by_type, now=now)
            ensure_weight_materialization(
                session, active_by_type, now=now, approved_by=actor_user_id
            )
            session.flush()

            return {
                "config_type": target.config_type,
                "version": target.version,
                "status": target.status,
                "content_hash": target.content_hash,
                "previous": previous,
                "effective_from": target.effective_from.isoformat(),
                "validation_warning_count": sum(
                    issue.level == "WARNING" for issue in validation.issues
                ),
                "reference_materialized": True,
            }

    def create_research_request(self, request: ResearchRequest, *, status: str = "RUNNING") -> None:
        with db_session() as session:
            existing = session.get(ResearchRequestModel, request.research_request_id)
            if existing is None:
                session.add(ResearchRequestModel(
                    research_request_id=request.research_request_id,
                    request_type=request.request_type,
                    target_factor_id=request.target_factor_id,
                    title=request.title,
                    question=request.question,
                    requested_by=request.requested_by,
                    status=status,
                    created_at=datetime.now(timezone.utc),
                ))
            else:
                existing.status = status

    def update_research_request(self, request_id: UUID, *, status: str, prefect_flow_run_id: UUID | None = None) -> None:
        with db_session() as session:
            row = session.get(ResearchRequestModel, request_id)
            if row is None:
                return
            row.status = status
            if prefect_flow_run_id is not None:
                row.prefect_flow_run_id = prefect_flow_run_id

    def save_research_evidence(self, items: list[ResearchEvidence]) -> None:
        if not items:
            return
        with db_session() as session:
            for item in items:
                if session.get(ResearchEvidenceModel, item.evidence_id) is not None:
                    continue
                session.add(ResearchEvidenceModel(
                    evidence_id=item.evidence_id,
                    research_request_id=item.research_request_id,
                    source_type=item.source_type,
                    source_tier=item.source_tier,
                    title=item.title,
                    url=item.url,
                    published_at=item.published_at,
                    retrieved_at=item.retrieved_at,
                    supports=item.supports,
                    claim_summary=item.claim_summary,
                    evidence_summary=item.evidence_summary,
                    quality_score=item.quality_score,
                    metadata_json=item.metadata,
                ))

    def save_factor_candidate(self, candidate: FactorResearchCandidate) -> None:
        with db_session() as session:
            row = session.get(FactorCandidateModel, candidate.candidate_id)
            if row is None:
                row = FactorCandidateModel(
                    candidate_id=candidate.candidate_id,
                    research_request_id=candidate.research_request_id,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(row)
            row.target_factor_id = candidate.target_factor_id
            row.candidate_type = candidate.candidate_type
            row.hypothesis = candidate.hypothesis
            row.mechanism = candidate.mechanism
            row.evidence_grade = candidate.evidence_grade
            row.proposed_config_patch = candidate.proposed_config_patch
            row.validation_required = candidate.validation_required
            row.status = candidate.status
            row.production_enabled = candidate.production_enabled
            row.updated_at = datetime.now(timezone.utc)

    def get_research_request(self, request_id: UUID) -> dict | None:
        with db_session() as session:
            row = session.get(ResearchRequestModel, request_id)
            if row is None:
                return None
            evidence = session.execute(
                select(ResearchEvidenceModel).where(ResearchEvidenceModel.research_request_id == request_id)
            ).scalars().all()
            candidates = session.execute(
                select(FactorCandidateModel).where(FactorCandidateModel.research_request_id == request_id)
            ).scalars().all()
            return {
                "research_request_id": str(row.research_request_id),
                "request_type": row.request_type,
                "target_factor_id": row.target_factor_id,
                "title": row.title,
                "question": row.question,
                "status": row.status,
                "created_at": row.created_at.isoformat(),
                "evidence": [
                    {"evidence_id": str(e.evidence_id), "title": e.title, "url": e.url, "source_tier": e.source_tier,
                     "quality_score": e.quality_score, "summary": e.evidence_summary}
                    for e in evidence
                ],
                "candidates": [
                    {"candidate_id": str(c.candidate_id), "candidate_type": c.candidate_type,
                     "target_factor_id": c.target_factor_id, "hypothesis": c.hypothesis, "mechanism": c.mechanism,
                     "evidence_grade": c.evidence_grade, "status": c.status,
                     "production_enabled": c.production_enabled, "validation_required": c.validation_required}
                    for c in candidates
                ],
            }

    def review_factor_candidate(
        self, candidate_id: UUID, *, action: str, reviewer_id: UUID | None, comment: str | None
    ) -> dict | None:
        with db_session() as session:
            row = session.get(FactorCandidateModel, candidate_id)
            if row is None:
                return None
            status_map = {
                "approve_shadow": "SHADOW",
                "reject": "REJECTED",
                "revise": "REVISION_REQUIRED",
            }
            row.status = status_map[action]
            row.production_enabled = False
            session.add(ApprovalRecordModel(
                candidate_id=candidate_id,
                action=action.upper(),
                reviewer_id=reviewer_id,
                comment=comment,
                created_at=datetime.now(timezone.utc),
            ))
            return {"candidate_id": str(candidate_id), "status": row.status, "production_enabled": False}

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
        with db_session() as session:
            session.add(AuditLogModel(
                actor_user_id=actor_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                before_json=before,
                after_json=after,
                request_id=request_id,
                created_at=datetime.now(timezone.utc),
            ))

    def list_audit(self, *, limit: int = 100) -> list[dict]:
        with db_session() as session:
            rows = session.execute(
                select(AuditLogModel).order_by(desc(AuditLogModel.created_at)).limit(max(1, min(limit, 500)))
            ).scalars().all()
            return [
                {
                    "audit_id": str(row.audit_id),
                    "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
                    "action": row.action,
                    "resource_type": row.resource_type,
                    "resource_id": row.resource_id,
                    "before": row.before_json,
                    "after": row.after_json,
                    "request_id": row.request_id,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def get_user_auth(self, username: str) -> dict | None:
        with db_session() as session:
            user = session.execute(select(UserModel).where(UserModel.username == username)).scalar_one_or_none()
            if user is None:
                return None
            roles = session.execute(
                select(UserRoleModel.role_code).where(UserRoleModel.user_id == user.user_id)
            ).scalars().all()
            return {
                "user_id": user.user_id,
                "username": user.username,
                "password_hash": user.password_hash,
                "is_active": user.is_active,
                "roles": list(roles),
            }

    def reserve_run(self, request) -> None:
        now = datetime.now(timezone.utc)
        with db_session() as session:
            existing = session.get(AnalysisRunModel, request.analysis_run_id)
            if existing is not None:
                return
            session.add(AnalysisRunModel(
                analysis_run_id=request.analysis_run_id,
                analysis_date=request.analysis_date or now.date(),
                as_of_ts=now,
                data_cutoff_ts=now,
                trigger_type=request.trigger_type.value,
                run_mode=request.run_mode.value,
                publish_mode=request.publish_mode.value,
                status=AnalysisStatus.PENDING.value,
                requested_by=request.requested_by,
                base_run_id=request.base_run_id,
                version_snapshot={},
                started_at=now,
            ))

    def create_run(self, context: AnalysisContext) -> None:
        now = datetime.now(timezone.utc)
        with db_session() as session:
            row = session.get(AnalysisRunModel, context.analysis_run_id)
            if row is None:
                row = AnalysisRunModel(analysis_run_id=context.analysis_run_id, started_at=now)
                session.add(row)
            row.analysis_date = context.analysis_date
            row.as_of_ts = context.as_of_ts
            row.data_cutoff_ts = context.data_cutoff_ts
            row.trigger_type = context.trigger_type.value
            row.run_mode = context.run_mode.value
            row.publish_mode = context.publish_mode.value
            row.status = AnalysisStatus.RUNNING.value
            row.requested_by = context.requested_by
            row.version_snapshot = context.versions.model_dump(mode="json")

    def list_runs(self, *, limit: int = 50) -> list[dict]:
        limit = max(1, min(limit, 200))
        with db_session() as session:
            rows = session.execute(
                select(AnalysisRunModel)
                .order_by(desc(AnalysisRunModel.started_at), desc(AnalysisRunModel.as_of_ts))
                .limit(limit)
            ).scalars().all()
            return [{
                "analysis_run_id": str(row.analysis_run_id),
                "analysis_date": row.analysis_date.isoformat(),
                "status": row.status,
                "trigger_type": row.trigger_type,
                "run_mode": row.run_mode,
                "publish_mode": row.publish_mode,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                "error_code": row.error_code,
            } for row in rows]

    def get_run_status(self, run_id: UUID) -> dict | None:
        with db_session() as session:
            row = session.get(AnalysisRunModel, run_id)
            if row is None:
                return None
            return {
                "analysis_run_id": str(run_id),
                "analysis_date": row.analysis_date.isoformat() if row.analysis_date else None,
                "as_of": row.as_of_ts.isoformat() if row.as_of_ts else None,
                "status": row.status,
                "coverage": row.coverage,
                "quality_gate": row.quality_gate,
                "error_code": row.error_code,
                "error_summary": row.error_summary,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            }

    def update_run(
        self,
        run_id: UUID,
        status: AnalysisStatus,
        quality: QualityGateResult | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        with db_session() as session:
            row = session.get(AnalysisRunModel, run_id)
            if row is None:
                return
            row.status = status.value
            row.error_code = error_code
            row.error_summary = error_summary
            if quality is not None:
                row.coverage = quality.overall_weighted_coverage
                row.quality_gate = quality.model_dump(mode="json")
            if status in {
                AnalysisStatus.PREVIEW_READY,
                AnalysisStatus.PUBLISHED,
                AnalysisStatus.QUALITY_FAILED,
                AnalysisStatus.FAILED,
                AnalysisStatus.CANCELLED,
                AnalysisStatus.SKIPPED,
            }:
                row.finished_at = datetime.now(timezone.utc)


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
        with db_session() as session:
            row = RunEventModel(
                analysis_run_id=analysis_run_id,
                stage=stage,
                event_type=event_type,
                status=status,
                level=level,
                message=message,
                progress=progress,
                details_json=details,
                created_at=created_at,
            )
            session.add(row)
            session.flush()
            return RunTraceEvent(
                trace_seq=row.trace_seq,
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

    def list_run_events(
        self, run_id: UUID, *, after_seq: int = 0, limit: int = 100
    ) -> list[RunTraceEvent]:
        limit = max(1, min(limit, 500))
        with db_session() as session:
            rows = session.execute(
                select(RunEventModel)
                .where(
                    RunEventModel.analysis_run_id == run_id,
                    RunEventModel.trace_seq > after_seq,
                )
                .order_by(RunEventModel.trace_seq.asc())
                .limit(limit)
            ).scalars().all()
            return [
                RunTraceEvent(
                    trace_seq=row.trace_seq,
                    analysis_run_id=row.analysis_run_id,
                    stage=row.stage,
                    event_type=row.event_type,
                    status=row.status,
                    level=row.level,
                    message=row.message,
                    progress=row.progress,
                    details=row.details_json or {},
                    created_at=row.created_at,
                )
                for row in rows
            ]


    def save_documents(self, documents) -> None:
        if not documents:
            return
        now = datetime.now(timezone.utc)
        with db_session() as session:
            for document in documents:
                content_hash = hashlib.sha256(
                    f"{document.title}\n{document.content}".encode("utf-8")
                ).hexdigest()
                existing = session.execute(
                    select(RawDocumentModel).where(
                        RawDocumentModel.source_id == document.source_id,
                        RawDocumentModel.content_hash == content_hash,
                    ).limit(1)
                ).scalar_one_or_none()
                if existing is not None:
                    continue
                session.add(RawDocumentModel(
                    document_id=document.document_id,
                    source_id=document.source_id,
                    source_url=document.url,
                    document_type="NEWS",
                    title=document.title,
                    raw_text=document.content,
                    language=document.language,
                    published_at=document.published_at,
                    retrieved_at=document.retrieved_at,
                    content_hash=content_hash,
                    raw_metadata={"source_tier": document.source_tier.value},
                    created_at=now,
                ))

    def get_documents(self, document_ids) -> list[NewsDocument]:
        ids = list(document_ids or [])
        if not ids:
            return []
        with db_session() as session:
            rows = session.execute(
                select(RawDocumentModel).where(RawDocumentModel.document_id.in_(ids))
            ).scalars().all()
            by_id = {row.document_id: row for row in rows}
            result: list[NewsDocument] = []
            for document_id in ids:
                row = by_id.get(document_id)
                if row is None:
                    continue
                tier = (row.raw_metadata or {}).get("source_tier", "C")
                try:
                    source_tier = SourceTier(tier)
                except ValueError:
                    source_tier = SourceTier.C
                result.append(NewsDocument(
                    document_id=row.document_id,
                    source_id=row.source_id,
                    source_tier=source_tier,
                    title=row.title,
                    content=row.raw_text,
                    published_at=row.published_at,
                    retrieved_at=row.retrieved_at,
                    url=row.source_url,
                    language=row.language or "zh-CN",
                ))
            return result

    def get_documents_for_window(
        self, *, start: datetime, end: datetime, retrieved_before: datetime, limit: int = 100
    ) -> list[NewsDocument]:
        limit = max(1, min(limit, 500))
        with db_session() as session:
            rows = session.execute(
                select(RawDocumentModel)
                .where(
                    RawDocumentModel.published_at.is_not(None),
                    RawDocumentModel.published_at >= start,
                    RawDocumentModel.published_at <= end,
                    RawDocumentModel.retrieved_at <= retrieved_before,
                )
                .order_by(RawDocumentModel.published_at.desc(), RawDocumentModel.retrieved_at.desc())
                .limit(limit)
            ).scalars().all()
            result = []
            for row in rows:
                tier = (row.raw_metadata or {}).get("source_tier", "C")
                try:
                    source_tier = SourceTier(tier)
                except ValueError:
                    source_tier = SourceTier.C
                result.append(NewsDocument(
                    document_id=row.document_id,
                    source_id=row.source_id,
                    source_tier=source_tier,
                    title=row.title,
                    content=row.raw_text,
                    published_at=row.published_at,
                    retrieved_at=row.retrieved_at,
                    url=row.source_url,
                    language=row.language or "zh-CN",
                ))
            return result

    def list_snapshots(self, *, start_date, end_date, published_only: bool = True) -> list[DashboardSnapshot]:
        with db_session() as session:
            stmt = (
                select(AnalysisSnapshotModel)
                .join(AnalysisRunModel, AnalysisRunModel.analysis_run_id == AnalysisSnapshotModel.analysis_run_id)
                .where(
                    AnalysisRunModel.analysis_date >= start_date,
                    AnalysisRunModel.analysis_date <= end_date,
                )
                .order_by(AnalysisRunModel.analysis_date.asc(), AnalysisRunModel.as_of_ts.asc())
            )
            if published_only:
                stmt = stmt.where(AnalysisSnapshotModel.published.is_(True))
            rows = session.execute(stmt).scalars().all()
            return [DashboardSnapshot.model_validate(row.snapshot_json) for row in rows]

    def save_calibration_report(self, result: CalibrationResult, *, requested_by: UUID | None = None) -> None:
        with db_session() as session:
            existing = session.get(CalibrationReportModel, result.calibration_id)
            payload = result.model_dump(mode="json")
            if existing is None:
                session.add(CalibrationReportModel(
                    calibration_id=result.calibration_id,
                    start_date=result.start_date,
                    end_date=result.end_date,
                    horizons=result.horizons,
                    requested_by=requested_by,
                    result_json=payload,
                    created_at=result.created_at,
                ))
            else:
                existing.result_json = payload

    def get_calibration_report(self, calibration_id: UUID) -> CalibrationResult | None:
        with db_session() as session:
            row = session.get(CalibrationReportModel, calibration_id)
            return CalibrationResult.model_validate(row.result_json) if row else None

    def save_factor_inputs(self, run_id: UUID, inputs: list[FactorInput]) -> None:
        now = datetime.now(timezone.utc)
        with db_session() as session:
            session.add_all([
                FactorInputModel(
                    analysis_run_id=run_id,
                    factor_id=item.factor_id,
                    asset_id=item.asset_id,
                    value=item.value,
                    observed_at=item.observed_at,
                    status=item.status.value,
                    reliability=item.reliability,
                    source_ids=item.source_ids,
                    warnings=item.warnings,
                    created_at=now,
                )
                for item in inputs
            ])

    def save_llm_runs(self, run_id: UUID, executions: list) -> None:
        if not executions:
            return
        with db_session() as session:
            session.add_all([
                LLMRunModel(
                    llm_run_id=item.execution_id,
                    analysis_run_id=run_id,
                    graph_name="event_intelligence",
                    node_name=None,
                    task_type=item.task_type,
                    model_tier=item.tier.value,
                    provider=item.provider,
                    model_alias=item.model_alias,
                    resolved_model=item.resolved_model,
                    reasoning_effort=item.reasoning_effort,
                    started_at=item.started_at,
                    finished_at=item.finished_at,
                    latency_ms=item.latency_ms,
                    input_tokens=item.input_tokens,
                    output_tokens=item.output_tokens,
                    schema_valid=item.schema_valid,
                    retry_count=item.retry_count,
                    fallback_from=item.fallback_from,
                    status=item.status,
                    error_code=item.error_code,
                    error_message=item.error_message,
                    metadata_json={},
                ) for item in executions
            ])

    def save_events(self, run_id: UUID, events: list[StructuredEvent]) -> None:
        now = datetime.now(timezone.utc)
        with db_session() as session:
            for event in events:
                session.add(EventModel(
                    event_id=event.event_id,
                    cluster_id=event.cluster_id,
                    analysis_run_id=run_id,
                    event_type=event.event_type,
                    canonical_title=event.canonical_title,
                    published_at=event.published_at,
                    effective_at=event.effective_at,
                    ingest_at=now,
                    verification_status=event.verification_status.value,
                    source_tier=event.source_tier.value,
                    strength=event.strength,
                    confidence=event.confidence,
                    novelty=event.novelty,
                    priced_in=event.priced_in,
                    implementation=event.implementation,
                    horizon=event.horizon.value,
                    half_life_days=event.half_life_days,
                    graph_version=event.graph_version,
                    prompt_version=event.prompt_version,
                    raw_json=event.model_dump(mode="json"),
                    created_at=now,
                ))
                for mapping in event.factors:
                    session.add(EventFactorLinkModel(
                        event_id=event.event_id,
                        factor_id=mapping.factor_id,
                        direction=mapping.direction,
                        mapping_confidence=mapping.mapping_confidence,
                        reason_tags=mapping.reason_tags,
                        asset_override_json=event.asset_overrides or None,
                        created_at=now,
                    ))


    def get_factor_inputs(self, run_id: UUID) -> list[FactorInput]:
        with db_session() as session:
            rows = session.execute(
                select(FactorInputModel).where(FactorInputModel.analysis_run_id == run_id)
            ).scalars().all()
            return [
                FactorInput(
                    factor_id=row.factor_id,
                    asset_id=row.asset_id,
                    value=row.value,
                    observed_at=row.observed_at,
                    status=row.status,
                    reliability=row.reliability,
                    source_ids=row.source_ids,
                    warnings=row.warnings,
                )
                for row in rows
            ]

    def get_events(self, run_id: UUID) -> list[StructuredEvent]:
        with db_session() as session:
            rows = session.execute(
                select(EventModel).where(EventModel.analysis_run_id == run_id)
            ).scalars().all()
            return [StructuredEvent.model_validate(row.raw_json) for row in rows]

    def save_factor_states(self, run_id: UUID, states: list[FactorState]) -> None:
        now = datetime.now(timezone.utc)
        with db_session() as session:
            session.add_all([
                FactorStateModel(
                    analysis_run_id=run_id,
                    factor_id=state.factor_id,
                    asset_id=state.asset_id,
                    as_of_ts=state.as_of_ts,
                    state=state.state,
                    reliability=state.reliability,
                    coverage=state.coverage,
                    status=state.status.value,
                    components=state.components.model_dump(mode="json"),
                    evidence_refs=state.evidence_refs,
                    quality_flags=state.quality_flags,
                    event_ids=[str(x) for x in state.event_ids],
                    logic_version=state.logic_version,
                    data_vintage=state.data_vintage,
                    created_at=now,
                )
                for state in states
            ])

    def get_factor_states(self, run_id: UUID) -> list[FactorState]:
        with db_session() as session:
            rows = session.execute(
                select(FactorStateModel).where(FactorStateModel.analysis_run_id == run_id)
            ).scalars().all()
            return [
                FactorState(
                    factor_id=row.factor_id,
                    asset_id=row.asset_id,
                    as_of_ts=row.as_of_ts,
                    state=row.state,
                    components=row.components,
                    reliability=row.reliability,
                    coverage=row.coverage,
                    status=row.status,
                    evidence_refs=row.evidence_refs,
                    quality_flags=row.quality_flags,
                    event_ids=row.event_ids,
                    logic_version=row.logic_version,
                    data_vintage=row.data_vintage,
                )
                for row in rows
            ]

    def latest_factor_states(
        self, before: datetime | None = None, *, lookback_hours: float | None = None
    ) -> dict[str, FactorState]:
        """Load the most recent state per (factor, asset) for last-known-good resolution.

        A lower time bound matters: factor_resolution discards any candidate older than the
        factor's max_stale_hours anyway, so scanning the full history only grows the query as
        replay backfills accumulate rows.
        """
        floor = lookback_hours if lookback_hours is not None else _LKG_SCAN_LOOKBACK_HOURS
        with db_session() as session:
            stmt = select(FactorStateModel)
            if before is not None:
                stmt = stmt.where(FactorStateModel.as_of_ts < before)
                if floor and floor > 0:
                    stmt = stmt.where(
                        FactorStateModel.as_of_ts >= before - timedelta(hours=float(floor))
                    )
            stmt = stmt.order_by(FactorStateModel.factor_id, FactorStateModel.asset_id, desc(FactorStateModel.as_of_ts))
            rows = session.execute(stmt).scalars().all()
            latest: dict[str, FactorState] = {}
            for row in rows:
                key = f"{row.factor_id}::{row.asset_id}" if row.asset_id else row.factor_id
                if key in latest:
                    continue
                latest[key] = FactorState(
                    factor_id=row.factor_id, asset_id=row.asset_id, as_of_ts=row.as_of_ts,
                    state=row.state, components=row.components, reliability=row.reliability,
                    coverage=row.coverage, status=row.status, evidence_refs=row.evidence_refs,
                    quality_flags=row.quality_flags, event_ids=row.event_ids,
                    logic_version=row.logic_version, data_vintage=row.data_vintage,
                )
            return latest

    def save_indicator_observations(self, observations: list[IndicatorObservation]) -> None:
        if not observations:
            return
        with db_session() as session:
            for item in observations:
                # Re-fetching a long historical window every day must not create a new
                # pseudo-vintage when the value itself did not change. Preserve a new
                # row only when the known value/definition/status actually changed.
                latest = session.execute(
                    select(IndicatorObservationModel).where(
                        IndicatorObservationModel.indicator_id == item.indicator_id,
                        IndicatorObservationModel.asset_id == item.asset_id,
                        IndicatorObservationModel.observation_date == item.observation_date,
                        IndicatorObservationModel.source_id == item.source_id,
                    ).order_by(IndicatorObservationModel.release_ts.desc()).limit(1)
                ).scalar_one_or_none()
                if latest is not None and (
                    float(latest.value) == float(item.value)
                    and latest.definition_version == item.definition_version
                    and latest.status == item.status.value
                ):
                    continue
                session.add(IndicatorObservationModel(
                    indicator_id=item.indicator_id, asset_id=item.asset_id,
                    observation_date=item.observation_date, release_ts=item.release_ts,
                    ingest_ts=item.ingest_ts, value=item.value, source_id=item.source_id,
                    revision_vintage=item.revision_vintage, definition_version=item.definition_version,
                    status=item.status.value, metadata_json=item.metadata,
                ))

    def get_indicator_history(
        self, indicator_id: str, *, as_of: datetime, asset_id: str | None = None, limit: int = 500
    ) -> list[IndicatorObservation]:
        with db_session() as session:
            rows = session.execute(
                select(IndicatorObservationModel)
                .where(
                    IndicatorObservationModel.indicator_id == indicator_id,
                    IndicatorObservationModel.asset_id == asset_id,
                    IndicatorObservationModel.release_ts <= as_of,
                )
                .order_by(IndicatorObservationModel.observation_date.desc(), IndicatorObservationModel.release_ts.desc())
                .limit(limit)
            ).scalars().all()
            # For each observation date, retain the latest vintage known by as_of.
            by_date = {}
            for row in rows:
                by_date.setdefault(row.observation_date, row)
            result = [IndicatorObservation(
                indicator_id=row.indicator_id, asset_id=row.asset_id, observation_date=row.observation_date,
                release_ts=row.release_ts, ingest_ts=row.ingest_ts, value=row.value, source_id=row.source_id,
                revision_vintage=row.revision_vintage, definition_version=row.definition_version,
                status=row.status, metadata=row.metadata_json,
            ) for row in by_date.values()]
            result.sort(key=lambda x: x.observation_date)
            return result

    def update_provider_health(
        self, provider_key: str, *, status: str, latency_ms: int | None, details: dict | None = None
    ) -> None:
        now = datetime.now(timezone.utc)
        with db_session() as session:
            row = session.get(ProviderHealthModel, provider_key)
            if row is None:
                row = ProviderHealthModel(provider_key=provider_key, checked_at=now, status=status,
                    consecutive_failures=0, details_json={})
                session.add(row)
            row.checked_at = now
            row.status = status
            row.latency_ms = latency_ms
            row.details_json = details or {}
            if status == "VALID":
                row.last_success_at = now
                row.consecutive_failures = 0
            else:
                row.consecutive_failures = int(row.consecutive_failures or 0) + 1

    def list_provider_health(self) -> list[dict]:
        with db_session() as session:
            rows = session.execute(select(ProviderHealthModel).order_by(ProviderHealthModel.provider_key)).scalars().all()
            return [{
                "provider_key": row.provider_key, "checked_at": row.checked_at.isoformat(),
                "status": row.status, "latency_ms": row.latency_ms,
                "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
                "consecutive_failures": row.consecutive_failures, "details": row.details_json or {},
            } for row in rows]

    def save_snapshot(self, snapshot: DashboardSnapshot, quality: QualityGateResult) -> None:
        now = datetime.now(timezone.utc)
        with db_session() as session:
            existing = session.get(AnalysisSnapshotModel, snapshot.analysis_run_id)
            payload = snapshot.model_dump(mode="json")
            if existing is None:
                session.add(AnalysisSnapshotModel(
                    analysis_run_id=snapshot.analysis_run_id,
                    snapshot_json=payload,
                    published=snapshot.published,
                    created_at=now,
                    published_at=now if snapshot.published else None,
                ))
            else:
                existing.snapshot_json = payload
                existing.published = snapshot.published
                existing.published_at = now if snapshot.published else existing.published_at
                # Re-saving the same run replaces its scores; without this the run accumulates a
                # second full set of asset_scores/contributions and every downstream aggregate
                # over the table double counts it.
                stale_ids = session.execute(
                    select(AssetScoreModel.asset_score_id).where(
                        AssetScoreModel.analysis_run_id == snapshot.analysis_run_id
                    )
                ).scalars().all()
                if stale_ids:
                    session.execute(
                        delete(ScoreContributionModel).where(
                            ScoreContributionModel.asset_score_id.in_(stale_ids)
                        )
                    )
                    session.execute(
                        delete(AssetScoreModel).where(
                            AssetScoreModel.analysis_run_id == snapshot.analysis_run_id
                        )
                    )
                session.flush()

            for score in snapshot.assets:
                session.add(AssetScoreModel(
                    asset_score_id=score.asset_score_id,
                    analysis_run_id=snapshot.analysis_run_id,
                    asset_id=score.asset_id,
                    as_of_ts=score.as_of_ts,
                    tactical_score=score.tactical_score,
                    swing_score=score.swing_score,
                    strategic_score=score.strategic_score,
                    direction_score=score.direction_score,
                    asset_score=score.score,
                    label=score.label,
                    confidence=score.confidence.confidence,
                    coverage=score.confidence.coverage_score / 100.0,
                    published=snapshot.published,
                    version_snapshot=score.versions.model_dump(mode="json"),
                    created_at=now,
                    published_at=now if snapshot.published else None,
                ))
                for contribution in score.contributions:
                    session.add(ScoreContributionModel(
                        asset_score_id=score.asset_score_id,
                        factor_id=contribution.factor_id,
                        factor_state=contribution.factor_state,
                        sensitivity=contribution.sensitivity,
                        reliability=contribution.reliability,
                        raw_contribution=contribution.raw_contribution,
                        after_conflict=contribution.after_conflict,
                        after_group_cap=contribution.after_group_cap,
                        rank_positive=contribution.rank_positive,
                        rank_negative=contribution.rank_negative,
                    ))

    def get_snapshot(self, run_id: UUID) -> DashboardSnapshot | None:
        with db_session() as session:
            row = session.get(AnalysisSnapshotModel, run_id)
            return DashboardSnapshot.model_validate(row.snapshot_json) if row else None

    def latest_published(self) -> DashboardSnapshot | None:
        """Return the published snapshot for the most recent *analysis date*.

        Ordering by publication time alone lets a manually published replay or an older preview
        take over the dashboard, because publishing does not demote the previous PUBLISHED row.
        Analysis date is the business ordering; publication time only breaks ties between two
        runs of the same day.
        """
        with db_session() as session:
            stmt = (
                select(AnalysisSnapshotModel)
                .join(
                    AnalysisRunModel,
                    AnalysisRunModel.analysis_run_id == AnalysisSnapshotModel.analysis_run_id,
                )
                .where(AnalysisSnapshotModel.published.is_(True))
                .order_by(
                    desc(AnalysisRunModel.analysis_date),
                    desc(AnalysisSnapshotModel.published_at),
                    desc(AnalysisSnapshotModel.created_at),
                )
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            return DashboardSnapshot.model_validate(row.snapshot_json) if row else None

    def latest_published_analysis_date(self) -> date | None:
        with db_session() as session:
            stmt = (
                select(AnalysisRunModel.analysis_date)
                .join(
                    AnalysisSnapshotModel,
                    AnalysisRunModel.analysis_run_id == AnalysisSnapshotModel.analysis_run_id,
                )
                .where(AnalysisSnapshotModel.published.is_(True))
                .order_by(desc(AnalysisRunModel.analysis_date))
                .limit(1)
            )
            return session.execute(stmt).scalars().first()

    def publish(self, run_id: UUID) -> DashboardSnapshot | None:
        with db_session() as session:
            row = session.get(AnalysisSnapshotModel, run_id)
            if row is None:
                return None
            payload = dict(row.snapshot_json)
            payload["published"] = True
            payload["status"] = AnalysisStatus.PUBLISHED.value
            row.snapshot_json = payload
            row.published = True
            row.published_at = datetime.now(timezone.utc)
            score_rows = session.execute(
                select(AssetScoreModel).where(AssetScoreModel.analysis_run_id == run_id)
            ).scalars().all()
            for score in score_rows:
                score.published = True
                score.published_at = row.published_at
            run = session.get(AnalysisRunModel, run_id)
            if run:
                run.status = AnalysisStatus.PUBLISHED.value
                run.finished_at = row.published_at
            return DashboardSnapshot.model_validate(payload)
