from __future__ import annotations

from pydantic import BaseModel

from widegold.domain.enums import ModelTier
from widegold.llm.providers.http_compatible import LLMProviderError, OpenAICompatibleHTTPProvider
from widegold.llm.router import ModelAlias, ModelRouter
from widegold.schemas.llm import StructuredLLMResult
from widegold.settings.app import get_settings


class LLMFallbackExhausted(RuntimeError):
    def __init__(self, task_type: str, executions: list, message: str):
        super().__init__(message)
        self.task_type = task_type
        self.executions = executions


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
            )
        raise RuntimeError(f"Unknown provider: {alias.provider}")

    async def structured_call(
        self,
        task_type: str,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
    ) -> StructuredLLMResult:
        executions = []
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
                    fallback_from=previous_alias,
                )
            except LLMProviderError as exc:
                executions.append(exc.execution)
                previous_alias = alias.alias
            except RuntimeError:
                previous_alias = alias.alias
                continue
        errors = "; ".join(f"{x.model_alias}:{x.error_message}" for x in executions) or "no configured provider"
        message = f"LLM fallback exhausted for {task_type}: {errors}"
        raise LLMFallbackExhausted(task_type, executions, message)


    def structured_call_sync(self, task_type: str, messages: list[dict[str, str]], schema: type[BaseModel]) -> StructuredLLMResult:
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.structured_call(task_type, messages, schema))
        raise RuntimeError("structured_call_sync cannot run inside an active event loop; use await structured_call")
