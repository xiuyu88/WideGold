from __future__ import annotations

from pydantic import BaseModel

from widegold.domain.enums import ModelTier
from widegold.llm.providers.http_compatible import LLMProviderError, OpenAICompatibleHTTPProvider
from widegold.llm.router import ModelAlias, ModelRouter
from widegold.schemas.llm import StructuredLLMResult
from widegold.settings.app import get_settings


class LLMFallbackExhausted(RuntimeError):
    def __init__(self, task_type: str, executions: list, message: str, skipped: list[str] | None = None):
        super().__init__(message)
        self.task_type = task_type
        self.executions = executions
        # Aliases that were never called (provider not configured).  Without this the operator
        # cannot tell "the model rejected us" from "the key was never set".
        self.skipped = skipped or []


class LLMService:
    """Capability router: Qwen -> DeepSeek -> third-party GPT, with normalized fallback traces."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.router = ModelRouter()

    def _provider(self, alias: ModelAlias) -> OpenAICompatibleHTTPProvider:
        if alias.provider == "qwen":
            if not self.settings.qwen_api_key or not self.settings.qwen_base_url:
                raise RuntimeError("Qwen provider is not configured")
            return OpenAICompatibleHTTPProvider(
                "qwen",
                self.settings.qwen_base_url,
                self.settings.qwen_api_key,
                max_output_tokens=self.settings.llm_max_output_tokens,
                schema_repair_attempts=self.settings.llm_schema_repair_attempts,
                transient_retry_attempts=self.settings.llm_transient_retry_attempts,
            )
        if alias.provider == "deepseek":
            if not self.settings.deepseek_api_key:
                raise RuntimeError("DeepSeek provider is not configured")
            return OpenAICompatibleHTTPProvider(
                "deepseek",
                self.settings.deepseek_base_url,
                self.settings.deepseek_api_key,
                deepseek_thinking_control=True,
                max_output_tokens=self.settings.llm_max_output_tokens,
                schema_repair_attempts=self.settings.llm_schema_repair_attempts,
                transient_retry_attempts=self.settings.llm_transient_retry_attempts,
            )
        if alias.provider == "openai_compatible":
            if not self.settings.gpt_compat_api_key:
                raise RuntimeError("Third-party GPT provider is not configured")
            return OpenAICompatibleHTTPProvider(
                "openai_compatible",
                self.settings.gpt_compat_base_url,
                self.settings.gpt_compat_api_key,
                include_reasoning_effort=True,
                max_output_tokens=self.settings.llm_max_output_tokens,
                schema_repair_attempts=self.settings.llm_schema_repair_attempts,
                transient_retry_attempts=self.settings.llm_transient_retry_attempts,
            )
        raise RuntimeError(f"Unknown provider: {alias.provider}")

    async def structured_call(
        self,
        task_type: str,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
    ) -> StructuredLLMResult:
        executions = []
        skipped: list[str] = []
        previous_alias: str | None = None
        for alias in self.router.route(task_type):
            try:
                provider = self._provider(alias)
                return await provider.structured_call(
                    model_alias=alias.alias,
                    model=alias.model,
                    tier=ModelTier(alias.tier),
                    task_type=task_type,
                    messages=messages,
                    schema=schema,
                    reasoning_effort=alias.reasoning_effort,
                    timeout_seconds=self.settings.llm_timeout_seconds,
                    fallback_from=previous_alias,
                )
            except LLMProviderError as exc:
                executions.append(exc.execution)
                previous_alias = alias.alias
            except RuntimeError as exc:
                # Provider not configured for this deployment: record it instead of failing silently.
                skipped.append(f"{alias.alias}({alias.provider}):{exc}")
                previous_alias = alias.alias
                continue
        errors = "; ".join(
            f"{x.model_alias}/{x.resolved_model}:{x.error_code}:{(x.error_message or '')[:200]}"
            for x in executions
        ) or "no configured provider"
        message = f"LLM fallback exhausted for {task_type}: {errors}"
        if skipped:
            message += f" | skipped: {'; '.join(skipped)}"
        raise LLMFallbackExhausted(task_type, executions, message, skipped=skipped)


    def structured_call_sync(self, task_type: str, messages: list[dict[str, str]], schema: type[BaseModel]) -> StructuredLLMResult:
        """Blocking entry point used by the graph nodes.

        LangGraph and Prefect may or may not already own an event loop on the calling thread, and
        which one it is depends on deployment details the graph nodes must not care about.  When a
        loop is already running the coroutine is handed to a private loop on a worker thread
        instead of failing the whole cluster.
        """
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.structured_call(task_type, messages, schema))

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: asyncio.run(self.structured_call(task_type, messages, schema))
            ).result()
