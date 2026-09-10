from pydantic import Field

from widegold.schemas.common import StrictModel


class ConfigIssue(StrictModel):
    level: str
    code: str
    message: str
    resource: str | None = None


class ConfigValidationResult(StrictModel):
    valid: bool
    factor_count: int
    calculator_count: int
    indicator_count: int
    required_indicator_count: int
    external_indicator_count: int
    issues: list[ConfigIssue] = Field(default_factory=list)


class DependencyCheck(StrictModel):
    name: str
    status: str
    required: bool
    latency_ms: float = 0.0
    message: str | None = None


class RuntimeReadiness(StrictModel):
    ready: bool
    degraded: bool = False
    checks: list[DependencyCheck] = Field(default_factory=list)
    config: ConfigValidationResult


class SystemDiagnostics(StrictModel):
    config: ConfigValidationResult
    readiness: RuntimeReadiness | None = None
    external_bridge_configured: bool = False
    external_indicators: list[dict] = Field(default_factory=list)
    providers: list[dict] = Field(default_factory=list)
    latest_published_run_id: str | None = None
    latest_analysis_date: str | None = None
    latest_quality_gate: dict | None = None


class ConfigActivationCommand(StrictModel):
    config_type: str
    version: str


class ConfigVersionSummary(StrictModel):
    config_version_id: str
    config_type: str
    version: str
    status: str
    content_hash: str
    effective_from: str
    effective_to: str | None = None
    created_by: str | None = None
    approved_by: str | None = None
    created_at: str
    approved_at: str | None = None


class ConfigVersionStageCommand(StrictModel):
    config_type: str
    version: str
    content: dict
    status: str = "DRAFT"


class ConfigVersionStageResult(StrictModel):
    config_type: str
    version: str
    status: str
    content_hash: str
    validation: ConfigValidationResult
    created: bool
