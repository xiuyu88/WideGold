from __future__ import annotations

import json
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel

from widegold.domain.enums import ModelTier
from widegold.schemas.llm import ModelExecution, StructuredLLMResult


class OpenAICompatibleHTTPProvider:
    def __init__(
        self,
        provider_name: str,
        base_url: str,
        api_key: str,
        include_reasoning_effort: bool = False,
        deepseek_thinking_control: bool = False,
        max_output_tokens: int | None = None,
    ):
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.include_reasoning_effort = include_reasoning_effort
        self.deepseek_thinking_control = deepseek_thinking_control
        self.max_output_tokens = max_output_tokens

    async def structured_call(
        self,
        *,
        model_alias: str,
        model: str,
        tier: ModelTier,
        task_type: str,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
        reasoning_effort: str,
        timeout_seconds: float = 60.0,
        fallback_from: str | None = None,
    ) -> StructuredLLMResult:
        started = datetime.now(timezone.utc)
        start_clock = perf_counter()
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        if self.max_output_tokens is not None and self.max_output_tokens > 0:
            payload["max_tokens"] = self.max_output_tokens

        if self.deepseek_thinking_control:
            # DeepSeek V4 enables thinking by default.  For low-cost extraction/classification
            # aliases, reasoning_effort=none explicitly disables thinking.  Reasoning aliases
            # keep thinking enabled and pass the supported low/high/max effort value.
            normalized_effort = reasoning_effort.strip().lower()
            if normalized_effort in {"none", "off", "disabled"}:
                payload["thinking"] = {"type": "disabled"}
            else:
                payload["thinking"] = {"type": "enabled"}
                payload["reasoning_effort"] = (
                    normalized_effort if normalized_effort in {"low", "high", "max"} else "low"
                )
        elif self.include_reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in content)
            data = schema.model_validate_json(content)
            usage = body.get("usage", {})
            finished = datetime.now(timezone.utc)
            execution = ModelExecution(
                provider=self.provider_name,
                model_alias=model_alias,
                resolved_model=model,
                tier=tier,
                task_type=task_type,
                reasoning_effort=reasoning_effort,
                started_at=started,
                finished_at=finished,
                latency_ms=int((perf_counter() - start_clock) * 1000),
                input_tokens=usage.get("prompt_tokens") or usage.get("input_tokens"),
                output_tokens=usage.get("completion_tokens") or usage.get("output_tokens"),
                schema_valid=True,
                fallback_from=fallback_from,
                status="SUCCESS",
            )
            return StructuredLLMResult(data=data, execution=execution)
        except Exception as exc:  # noqa: BLE001 - provider boundary returns normalized failure upstream
            finished = datetime.now(timezone.utc)
            execution = ModelExecution(
                provider=self.provider_name,
                model_alias=model_alias,
                resolved_model=model,
                tier=tier,
                task_type=task_type,
                reasoning_effort=reasoning_effort,
                started_at=started,
                finished_at=finished,
                latency_ms=int((perf_counter() - start_clock) * 1000),
                schema_valid=False,
                fallback_from=fallback_from,
                status="FAILED",
                error_code="LLM_PROVIDER_CALL_FAILED",
                error_message=str(exc),
            )
            raise LLMProviderError(str(exc), execution) from exc


class LLMProviderError(RuntimeError):
    def __init__(self, message: str, execution: ModelExecution):
        super().__init__(message)
        self.execution = execution
