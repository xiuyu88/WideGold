from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from widegold.domain.enums import ModelTier
from widegold.schemas.llm import ModelExecution, StructuredLLMResult

# HTTP failures that will never succeed by retrying the same alias (wrong model name, wrong key,
# wrong base_url, rejected request body).  They must fail fast so the fallback chain does not
# multiply cost, and so the recorded error_code points straight at the misconfiguration.
_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 405, 422}

# Rate limiting and upstream capacity problems are transient: retrying the same alias with a short
# backoff is cheaper and more likely to succeed than immediately escalating to a costlier model.
_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

_FENCE_OPEN_RE = re.compile(r"^```[A-Za-z0-9_-]*\s*")
_FENCE_CLOSE_RE = re.compile(r"\s*```$")


def _schema_instruction(schema: type[BaseModel]) -> str:
    """Return the contract the model must satisfy.

    The structured-output contract must be *sent to the model*.  Validating a response against a
    schema the model never saw produces syntactically valid JSON with the wrong field names, which
    fails validation on every attempt and exhausts the whole fallback chain.
    """
    return (
        "你必须只输出一个 JSON 对象，且严格符合下面的 JSON Schema。\n"
        "硬性要求：\n"
        "1. 不要输出 Markdown 代码块、前后缀说明或任何解释文字，只输出 JSON 本身；\n"
        "2. 不得出现 Schema 之外的任何字段（多余字段会导致结果被丢弃）；\n"
        "3. required 字段必须全部给出；\n"
        "4. 材料中无法确定的可选字段请省略或填 null，不要编造；\n"
        "5. 枚举字段必须使用 Schema 中列出的字面量。\n"
        "JSON Schema:\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )


def _strip_json_text(content: str) -> str:
    """Recover the JSON object from a response that may carry fences or commentary."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = _FENCE_CLOSE_RE.sub("", _FENCE_OPEN_RE.sub("", text)).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Response contains no JSON object")
    return text[start : end + 1]


class OpenAICompatibleHTTPProvider:
    def __init__(
        self,
        provider_name: str,
        base_url: str,
        api_key: str,
        include_reasoning_effort: bool = False,
        deepseek_thinking_control: bool = False,
        max_output_tokens: int | None = None,
        schema_repair_attempts: int = 1,
        thinking_token_headroom: int = 2048,
        transient_retry_attempts: int = 2,
        transient_retry_base_seconds: float = 1.0,
    ):
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.include_reasoning_effort = include_reasoning_effort
        self.deepseek_thinking_control = deepseek_thinking_control
        self.max_output_tokens = max_output_tokens
        self.schema_repair_attempts = max(0, int(schema_repair_attempts))
        self.thinking_token_headroom = max(0, int(thinking_token_headroom))
        self.transient_retry_attempts = max(0, int(transient_retry_attempts))
        self.transient_retry_base_seconds = max(0.0, float(transient_retry_base_seconds))

    def _build_payload(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        reasoning_effort: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        thinking_enabled = False
        if self.deepseek_thinking_control:
            # DeepSeek V4 enables thinking by default.  For low-cost extraction/classification
            # aliases, reasoning_effort=none explicitly disables thinking.  Reasoning aliases
            # keep thinking enabled and pass the supported low/high/max effort value.
            normalized_effort = reasoning_effort.strip().lower()
            if normalized_effort in {"none", "off", "disabled"}:
                payload["thinking"] = {"type": "disabled"}
            else:
                thinking_enabled = True
                payload["thinking"] = {"type": "enabled"}
                payload["reasoning_effort"] = (
                    normalized_effort if normalized_effort in {"low", "high", "max"} else "low"
                )
        elif self.include_reasoning_effort:
            thinking_enabled = reasoning_effort.strip().lower() not in {"none", "off", "disabled"}
            payload["reasoning_effort"] = reasoning_effort

        if self.max_output_tokens is not None and self.max_output_tokens > 0:
            # Reasoning tokens are charged against the same completion budget.  Without headroom a
            # thinking model spends the budget before emitting the JSON body and returns empty
            # content, which is indistinguishable from a schema failure downstream.
            budget = self.max_output_tokens
            if thinking_enabled:
                budget += self.thinking_token_headroom
            payload["max_tokens"] = budget
        return payload

    async def _post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        response = await client.post(
            f"{self.base_url}/chat/completions", headers=headers, json=payload
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _retry_after_seconds(response: httpx.Response, default: float) -> float:
        raw = response.headers.get("retry-after")
        try:
            # Only the numeric form is honored; an HTTP-date would usually be far in the future
            # and blocking a worker on it is worse than falling through to the next alias.
            return min(30.0, max(0.0, float(raw))) if raw is not None else default
        except (TypeError, ValueError):
            return default

    async def _post_with_transient_retry(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                return await self._post(client, payload, headers)
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code not in _TRANSIENT_STATUS or attempt >= self.transient_retry_attempts:
                    raise
                delay = self._retry_after_seconds(
                    exc.response, self.transient_retry_base_seconds * (2**attempt)
                )
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt >= self.transient_retry_attempts:
                    raise
                delay = self.transient_retry_base_seconds * (2**attempt)
            attempt += 1
            if delay > 0:
                await asyncio.sleep(delay)

    @staticmethod
    def _message_content(body: dict[str, Any]) -> str:
        choices = body.get("choices") or []
        if not choices:
            raise ValueError("Response has no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
                for item in content
            )
        if not content or not str(content).strip():
            finish_reason = choices[0].get("finish_reason")
            raise ValueError(
                f"Empty completion content (finish_reason={finish_reason}); "
                "the output token budget was likely consumed before the JSON body"
            )
        return str(content)

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
        timeout_seconds: float = 90.0,
        fallback_from: str | None = None,
    ) -> StructuredLLMResult:
        started = datetime.now(timezone.utc)
        start_clock = perf_counter()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        # The schema contract is appended as its own system turn so callers keep their task prompts
        # free of transport concerns.
        request_messages = [*messages, {"role": "system", "content": _schema_instruction(schema)}]

        attempts = 0
        usage: dict[str, Any] = {}
        error_code = "LLM_PROVIDER_CALL_FAILED"
        last_error: Exception | None = None

        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                while attempts <= self.schema_repair_attempts:
                    payload = self._build_payload(
                        model=model, messages=request_messages, reasoning_effort=reasoning_effort
                    )
                    attempts += 1
                    try:
                        body = await self._post_with_transient_retry(client, payload, headers)
                    except httpx.HTTPStatusError as exc:
                        status_code = exc.response.status_code
                        error_code = f"LLM_HTTP_{status_code}"
                        detail = exc.response.text[:500]
                        raise RuntimeError(
                            f"HTTP {status_code} from {self.provider_name} for model {model}: {detail}"
                        ) from exc
                    except httpx.HTTPError:
                        error_code = "LLM_TRANSPORT_ERROR"
                        raise

                    usage = body.get("usage", {}) or {}
                    raw = ""
                    try:
                        raw = self._message_content(body)
                        data = schema.model_validate_json(_strip_json_text(raw))
                    except (ValidationError, ValueError) as exc:
                        error_code = "LLM_SCHEMA_INVALID"
                        last_error = exc
                        if attempts > self.schema_repair_attempts:
                            raise RuntimeError(
                                f"Structured output invalid after {attempts} attempt(s): {exc}"
                            ) from exc
                        # One in-place repair on the same alias is cheaper than falling through to
                        # the next (usually more expensive) alias with the same broken prompt.
                        repair_turns: list[dict[str, str]] = []
                        if raw.strip():
                            repair_turns.append({"role": "assistant", "content": raw[:4000]})
                        repair_turns.append(
                            {
                                "role": "user",
                                "content": (
                                    "上一次输出不符合要求，错误信息如下：\n"
                                    f"{str(exc)[:1200]}\n"
                                    "请只重新输出修正后的 JSON 对象，不要任何解释文字。"
                                ),
                            }
                        )
                        request_messages = [*request_messages, *repair_turns]
                        continue

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
                        retry_count=attempts - 1,
                        fallback_from=fallback_from,
                        status="SUCCESS",
                    )
                    return StructuredLLMResult(data=data, execution=execution)

            raise RuntimeError(f"Structured output invalid: {last_error}")
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
                input_tokens=usage.get("prompt_tokens") or usage.get("input_tokens"),
                output_tokens=usage.get("completion_tokens") or usage.get("output_tokens"),
                schema_valid=False,
                retry_count=max(0, attempts - 1),
                fallback_from=fallback_from,
                status="FAILED",
                error_code=error_code,
                error_message=str(exc)[:2000],
            )
            retryable = error_code not in {f"LLM_HTTP_{code}" for code in _NON_RETRYABLE_STATUS}
            raise LLMProviderError(str(exc), execution, retryable=retryable) from exc


class LLMProviderError(RuntimeError):
    def __init__(self, message: str, execution: ModelExecution, retryable: bool = True):
        super().__init__(message)
        self.execution = execution
        self.retryable = retryable
