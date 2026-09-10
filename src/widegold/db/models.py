from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from widegold.db.base import Base


# IAM -------------------------------------------------------------------------
class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"), {"schema": "iam"})
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoleModel(Base):
    __tablename__ = "roles"
    __table_args__ = {"schema": "iam"}
    role_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class UserRoleModel(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "role_code", name="pk_user_roles"),
        {"schema": "iam"},
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.users.user_id"), nullable=False)
    role_code: Mapped[str] = mapped_column(String(32), ForeignKey("iam.roles.role_code"), nullable=False)


# REFERENCE -------------------------------------------------------------------
class AssetModel(Base):
    __tablename__ = "assets"
    __table_args__ = {"schema": "reference"}
    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    index_code: Mapped[str | None] = mapped_column(String(32))
    exchange: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FactorDefinitionModel(Base):
    __tablename__ = "factor_definitions"
    __table_args__ = {"schema": "reference"}
    factor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))
    semantic_positive: Mapped[str | None] = mapped_column(Text)
    semantic_negative: Mapped[str | None] = mapped_column(Text)
    evidence_grade: Mapped[str] = mapped_column(String(8), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(16), default="APPROVED", nullable=False)
    correlation_group: Mapped[str | None] = mapped_column(String(64))
    update_frequency: Mapped[str | None] = mapped_column(String(32))
    max_age_seconds: Mapped[int | None] = mapped_column(Integer)
    logic_version: Mapped[str] = mapped_column(String(32), nullable=False)
    production_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AssetFactorWeightModel(Base):
    __tablename__ = "asset_factor_weights"
    __table_args__ = (
        Index("ix_asset_factor_weights_lookup", "asset_id", "factor_id", "horizon", "effective_from"),
        {"schema": "reference"},
    )
    weight_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    asset_id: Mapped[str] = mapped_column(String(64), ForeignKey("reference.assets.asset_id"), nullable=False)
    factor_id: Mapped[str] = mapped_column(String(64), ForeignKey("reference.factor_definitions.factor_id"), nullable=False)
    horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    sensitivity: Mapped[float] = mapped_column(Float, nullable=False)
    max_contribution: Mapped[float | None] = mapped_column(Float)
    weight_version: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rationale: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DataSourceModel(Base):
    __tablename__ = "data_sources"
    __table_args__ = {"schema": "reference"}
    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_tier: Mapped[str] = mapped_column(String(8), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    license_notes: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConfigVersionModel(Base):
    __tablename__ = "config_versions"
    __table_args__ = (
        UniqueConstraint("config_type", "version", name="uq_config_type_version"),
        Index(
            "uq_config_versions_active_type",
            "config_type",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        {"schema": "reference"},
    )
    config_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    config_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    content_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    approved_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IndicatorDefinitionModel(Base):
    __tablename__ = "indicator_definitions"
    __table_args__ = {"schema": "reference"}
    indicator_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    frequency: Mapped[str] = mapped_column(String(32), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IndicatorObservationModel(Base):
    __tablename__ = "indicator_observations"
    __table_args__ = (
        Index("ix_indicator_obs_lookup", "indicator_id", "asset_id", "observation_date", "release_ts"),
        UniqueConstraint("indicator_id", "asset_id", "observation_date", "release_ts", "source_id", name="uq_indicator_vintage"),
        {"schema": "market"},
    )
    indicator_observation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    indicator_id: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(64))
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    release_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingest_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision_vintage: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class ProviderHealthModel(Base):
    __tablename__ = "provider_health"
    __table_args__ = {"schema": "ops"}
    provider_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    details_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


# RAW -------------------------------------------------------------------------
class RawDocumentModel(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("source_id", "content_hash", name="uq_document_source_hash"),
        Index("ix_raw_documents_published", "published_at"),
        {"schema": "raw"},
    )
    document_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="zh-CN", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# MARKET ----------------------------------------------------------------------
class MarketPriceDailyModel(Base):
    __tablename__ = "prices_daily"
    __table_args__ = (
        PrimaryKeyConstraint("instrument_id", "trade_date", "source_id", name="pk_prices_daily"),
        {"schema": "market"},
    )
    instrument_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ingest_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MacroObservationModel(Base):
    __tablename__ = "macro_observations"
    __table_args__ = (
        Index("ix_macro_series_observation", "series_id", "observation_date"),
        Index("ix_macro_series_release", "series_id", "release_ts"),
        {"schema": "market"},
    )
    observation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    series_id: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    release_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingest_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    revision_vintage: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(64), nullable=False)
    is_preliminary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class FactorInputModel(Base):
    """Normalized provider-to-factor observation boundary.

    This generic table keeps real provider implementation decoupled from the factor engine while
    dedicated market tables preserve raw/point-in-time facts where needed.
    """
    __tablename__ = "factor_inputs"
    __table_args__ = (
        Index("ix_factor_inputs_lookup", "factor_id", "observed_at"),
        {"schema": "market"},
    )
    factor_input_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    analysis_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    factor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(Float)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reliability: Mapped[float] = mapped_column(Float, nullable=False)
    source_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    warnings: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# OPS -------------------------------------------------------------------------
class AnalysisRunModel(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_analysis_runs_date_status", "analysis_date", "status"),
        {"schema": "ops"},
    )
    analysis_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    prefect_flow_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    analysis_date: Mapped[date] = mapped_column(Date, nullable=False)
    as_of_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_cutoff_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    run_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    publish_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    base_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    version_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    coverage: Mapped[float | None] = mapped_column(Float)
    quality_gate: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)


class RunEventModel(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        Index("ix_run_events_run_seq", "analysis_run_id", "trace_seq"),
        {"schema": "ops"},
    )

    trace_seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    progress: Mapped[float | None] = mapped_column(Float)
    details_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnalysisSnapshotModel(Base):
    __tablename__ = "analysis_snapshots"
    __table_args__ = (Index("ix_analysis_snapshots_published", "published", "created_at"), {"schema": "ops"})
    analysis_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataFetchRunModel(Base):
    __tablename__ = "data_fetch_runs"
    __table_args__ = {"schema": "ops"}
    fetch_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    analysis_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    rows_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_resource", "resource_type", "resource_id"), {"schema": "ops"})
    audit_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_json: Mapped[dict | None] = mapped_column(JSONB)
    after_json: Mapped[dict | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# INTEL -----------------------------------------------------------------------
class EventModel(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_run_published", "analysis_run_id", "published_at"), {"schema": "intel"})
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    cluster_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    analysis_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_title: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingest_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_tier: Mapped[str] = mapped_column(String(8), nullable=False)
    strength: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    novelty: Mapped[float] = mapped_column(Float, nullable=False)
    priced_in: Mapped[float] = mapped_column(Float, nullable=False)
    implementation: Mapped[float] = mapped_column(Float, nullable=False)
    horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    half_life_days: Mapped[float] = mapped_column(Float, nullable=False)
    parent_event_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    graph_version: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventFactorLinkModel(Base):
    __tablename__ = "event_factor_links"
    __table_args__ = {"schema": "intel"}
    link_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("intel.events.event_id"), nullable=False)
    factor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    mapping_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason_tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    asset_override_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LLMRunModel(Base):
    __tablename__ = "llm_runs"
    __table_args__ = (Index("ix_llm_runs_analysis", "analysis_run_id"), {"schema": "intel"})
    llm_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    analysis_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    graph_name: Mapped[str | None] = mapped_column(String(64))
    node_name: Mapped[str | None] = mapped_column(String(64))
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    model_tier: Mapped[str] = mapped_column(String(8), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_model: Mapped[str] = mapped_column(String(128), nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    schema_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fallback_from: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


# DECISION --------------------------------------------------------------------
class FactorStateModel(Base):
    __tablename__ = "factor_states"
    __table_args__ = (Index("ix_factor_states_lookup", "factor_id", "as_of_ts"), {"schema": "decision"})
    factor_state_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    analysis_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    factor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(64))
    as_of_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[float] = mapped_column(Float, nullable=False)
    reliability: Mapped[float] = mapped_column(Float, nullable=False)
    coverage: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    components: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    quality_flags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    event_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    logic_version: Mapped[str] = mapped_column(String(32), nullable=False)
    data_vintage: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AssetScoreModel(Base):
    __tablename__ = "asset_scores"
    __table_args__ = (Index("ix_asset_scores_asset_published", "asset_id", "published", "as_of_ts"), {"schema": "decision"})
    asset_score_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    analysis_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tactical_score: Mapped[float] = mapped_column(Float, nullable=False)
    swing_score: Mapped[float] = mapped_column(Float, nullable=False)
    strategic_score: Mapped[float] = mapped_column(Float, nullable=False)
    direction_score: Mapped[float] = mapped_column(Float, nullable=False)
    asset_score: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    coverage: Mapped[float] = mapped_column(Float, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supersedes_score_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    version_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScoreContributionModel(Base):
    __tablename__ = "score_contributions"
    __table_args__ = {"schema": "decision"}
    contribution_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    asset_score_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("decision.asset_scores.asset_score_id"), nullable=False)
    factor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    factor_state: Mapped[float] = mapped_column(Float, nullable=False)
    sensitivity: Mapped[float] = mapped_column(Float, nullable=False)
    reliability: Mapped[float] = mapped_column(Float, nullable=False)
    raw_contribution: Mapped[float] = mapped_column(Float, nullable=False)
    after_conflict: Mapped[float] = mapped_column(Float, nullable=False)
    after_group_cap: Mapped[float] = mapped_column(Float, nullable=False)
    rank_positive: Mapped[int | None] = mapped_column(Integer)
    rank_negative: Mapped[int | None] = mapped_column(Integer)


class ReportModel(Base):
    __tablename__ = "reports"
    __table_args__ = {"schema": "decision"}
    report_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    analysis_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# RESEARCH --------------------------------------------------------------------
class CalibrationReportModel(Base):
    __tablename__ = "calibration_reports"
    __table_args__ = (Index("ix_calibration_reports_range", "start_date", "end_date", "created_at"), {"schema": "research"})
    calibration_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizons: Mapped[list] = mapped_column(JSONB, nullable=False)
    requested_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchRequestModel(Base):
    __tablename__ = "requests"
    __table_args__ = {"schema": "research"}
    research_request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    request_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_factor_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    prefect_flow_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    langgraph_thread_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchEvidenceModel(Base):
    __tablename__ = "evidence_items"
    __table_args__ = {"schema": "research"}
    evidence_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    research_request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_tier: Mapped[str] = mapped_column(String(8), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    supports: Mapped[bool | None] = mapped_column(Boolean)
    claim_summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class FactorCandidateModel(Base):
    __tablename__ = "factor_candidates"
    __table_args__ = {"schema": "research"}
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    research_request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    target_factor_id: Mapped[str | None] = mapped_column(String(64))
    candidate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    mechanism: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_grade: Mapped[str] = mapped_column(String(8), nullable=False)
    proposed_config_patch: Mapped[dict] = mapped_column(JSONB, nullable=False)
    validation_required: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    production_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApprovalRecordModel(Base):
    __tablename__ = "approval_records"
    __table_args__ = {"schema": "research"}
    approval_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# SCENARIO --------------------------------------------------------------------
class ScenarioRunModel(Base):
    __tablename__ = "runs"
    __table_args__ = {"schema": "scenario"}
    scenario_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assumption_text: Mapped[str] = mapped_column(Text, nullable=False)
    base_analysis_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    graph_thread_id: Mapped[str | None] = mapped_column(String(128))
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
