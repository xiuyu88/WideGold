from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = Field(default="development", validation_alias="WIDEGOLD_ENV")
    database_url: str = Field(
        default="postgresql+psycopg://widegold:widegold@localhost:5432/widegold",
        validation_alias="WIDEGOLD_DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="WIDEGOLD_REDIS_URL")
    persistence: str = Field(default="memory", validation_alias="WIDEGOLD_PERSISTENCE")
    runtime_config_source: str = Field(default="auto", validation_alias="WIDEGOLD_RUNTIME_CONFIG_SOURCE")
    runtime_config_cache_ttl_seconds: float = Field(
        default=5.0, validation_alias="WIDEGOLD_RUNTIME_CONFIG_CACHE_TTL_SECONDS"
    )
    runtime_config_generation_poll_seconds: float = Field(
        default=1.0, validation_alias="WIDEGOLD_RUNTIME_CONFIG_GENERATION_POLL_SECONDS"
    )
    orchestration_mode: str = Field(default="direct", validation_alias="WIDEGOLD_ORCHESTRATION_MODE")
    data_mode: str = Field(default="mock", validation_alias="WIDEGOLD_DATA_MODE")
    factor_feed_url: str | None = Field(default=None, validation_alias="WIDEGOLD_FACTOR_FEED_URL")
    fred_api_key: str | None = Field(default=None, validation_alias="FRED_API_KEY")
    external_indicator_url: str | None = Field(
        default=None, validation_alias="WIDEGOLD_EXTERNAL_INDICATOR_URL"
    )
    external_indicator_api_key: str | None = Field(
        default=None, validation_alias="WIDEGOLD_EXTERNAL_INDICATOR_API_KEY"
    )
    ingest_api_key: str | None = Field(
        default=None, validation_alias="WIDEGOLD_INGEST_API_KEY"
    )
    event_graph_mode: str = Field(default="mock", validation_alias="WIDEGOLD_EVENT_GRAPH_MODE")
    explanation_mode: str = Field(default="deterministic", validation_alias="WIDEGOLD_EXPLANATION_MODE")
    news_mode: str = Field(default="disabled", validation_alias="WIDEGOLD_NEWS_MODE")

    prefect_api_url: str = Field(default="http://localhost:4200/api", validation_alias="PREFECT_API_URL")
    prefect_deployment_name: str = Field(
        default="widegold-close-analysis/close-analysis",
        validation_alias="WIDEGOLD_PREFECT_DEPLOYMENT_NAME",
    )
    prefect_research_deployment_name: str = Field(
        default="widegold-factor-research/factor-research",
        validation_alias="WIDEGOLD_PREFECT_RESEARCH_DEPLOYMENT_NAME",
    )
    prefect_replay_deployment_name: str = Field(
        default="widegold-replay-backfill/replay-backfill",
        validation_alias="WIDEGOLD_PREFECT_REPLAY_DEPLOYMENT_NAME",
    )
    prefect_calibration_deployment_name: str = Field(
        default="widegold-calibration-review/calibration-review",
        validation_alias="WIDEGOLD_PREFECT_CALIBRATION_DEPLOYMENT_NAME",
    )

    qwen_api_key: str | None = Field(default=None, validation_alias="QWEN_API_KEY")
    qwen_base_url: str | None = Field(default=None, validation_alias="QWEN_BASE_URL")
    deepseek_api_key: str | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", validation_alias="DEEPSEEK_BASE_URL")
    llm_max_output_tokens: int = Field(default=1200, validation_alias="WIDEGOLD_LLM_MAX_OUTPUT_TOKENS")
    gpt_compat_api_key: str | None = Field(default=None, validation_alias="GPT_COMPAT_API_KEY")
    gpt_compat_base_url: str = Field(default="https://ca.memofun.net/v1", validation_alias="GPT_COMPAT_BASE_URL")
    gpt_compat_model: str = Field(default="gpt-5.6-sol", validation_alias="GPT_COMPAT_MODEL")

    search_provider: str | None = Field(default=None, validation_alias="SEARCH_PROVIDER")
    search_api_key: str | None = Field(default=None, validation_alias="SEARCH_API_KEY")
    search_base_url: str | None = Field(default=None, validation_alias="SEARCH_BASE_URL")
    research_mode: str = Field(default="mock", validation_alias="WIDEGOLD_RESEARCH_MODE")

    trace_frontend_enabled: bool = Field(default=True, validation_alias="WIDEGOLD_TRACE_FRONTEND_ENABLED")
    trace_sse_poll_seconds: float = Field(default=1.0, validation_alias="WIDEGOLD_TRACE_SSE_POLL_SECONDS")
    trace_page_size: int = Field(default=100, validation_alias="WIDEGOLD_TRACE_PAGE_SIZE")

    auth_mode: str = Field(default="disabled", validation_alias="WIDEGOLD_AUTH_MODE")
    session_ttl_seconds: int = Field(default=28800, validation_alias="WIDEGOLD_SESSION_TTL_SECONDS")
    cookie_secure: bool = Field(default=False, validation_alias="WIDEGOLD_COOKIE_SECURE")
    bootstrap_admin_username: str = Field(default="admin", validation_alias="WIDEGOLD_BOOTSTRAP_ADMIN_USERNAME")
    bootstrap_admin_password: str | None = Field(default=None, validation_alias="WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD")

    run_lock_enabled: bool = Field(default=False, validation_alias="WIDEGOLD_RUN_LOCK_ENABLED")
    run_lock_ttl_seconds: int = Field(default=3600, validation_alias="WIDEGOLD_RUN_LOCK_TTL_SECONDS")
    dashboard_cache_enabled: bool = Field(default=False, validation_alias="WIDEGOLD_DASHBOARD_CACHE_ENABLED")
    dashboard_cache_ttl_seconds: int = Field(default=300, validation_alias="WIDEGOLD_DASHBOARD_CACHE_TTL_SECONDS")

    langgraph_checkpoint_mode: str = Field(default="memory", validation_alias="WIDEGOLD_LANGGRAPH_CHECKPOINT_MODE")
    langgraph_database_url: str = Field(
        default="postgresql://widegold:widegold@localhost:5432/langgraph",
        validation_alias="WIDEGOLD_LANGGRAPH_DATABASE_URL",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
