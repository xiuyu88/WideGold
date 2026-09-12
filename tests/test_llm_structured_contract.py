"""Regression tests for the P0 failure: 78 LLM calls producing 0 structured events.

Root cause was that the Pydantic schema used for validation was never sent to the model, so every
response failed ``extra=forbid`` validation and exhausted the whole fallback chain.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from widegold.domain.enums import ModelTier, SourceTier
from widegold.llm.providers.http_compatible import (
    LLMProviderError,
    OpenAICompatibleHTTPProvider,
    _strip_json_text,
)
from widegold.schemas.events import EventExtractionOutput, NewsDocument
from widegold.services.news_clustering import cluster_news_documents
from widegold.settings.app import get_settings
from widegold.settings.config import news_config

MESSAGES = [{"role": "system", "content": "抽取事件"}, {"role": "user", "content": "{}"}]


def _body(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }


def _provider(**kwargs) -> OpenAICompatibleHTTPProvider:
    return OpenAICompatibleHTTPProvider(
        "deepseek", "https://example.invalid/v1", "test-key", **kwargs
    )


def _call(provider: OpenAICompatibleHTTPProvider):
    return asyncio.run(
        provider.structured_call(
            model_alias="deepseek_fast",
            model="deepseek-v4-flash",
            tier=ModelTier.L2,
            task_type="extraction",
            messages=MESSAGES,
            schema=EventExtractionOutput,
            reasoning_effort="none",
        )
    )


def test_output_schema_is_sent_to_the_model():
    provider = _provider()
    captured: dict = {}

    async def fake_post(client, payload, headers):
        captured["payload"] = payload
        return _body('{"event_detected": false}')

    provider._post = fake_post
    result = _call(provider)

    assert result.data.event_detected is False
    system_turns = [
        turn["content"] for turn in captured["payload"]["messages"] if turn["role"] == "system"
    ]
    # Without the field names in the request the model cannot produce a schema-valid object.
    assert any("event_detected" in turn for turn in system_turns)


def test_fenced_json_response_is_accepted():
    provider = _provider()

    async def fake_post(client, payload, headers):
        return _body('```json\n{"event_detected": true, "event_type": "CN_LIQUIDITY"}\n```')

    provider._post = fake_post
    assert _call(provider).data.event_type == "CN_LIQUIDITY"


def test_invalid_output_is_repaired_once_on_the_same_alias():
    provider = _provider(schema_repair_attempts=1)
    payloads: list[dict] = []

    async def fake_post(client, payload, headers):
        payloads.append(payload)
        if len(payloads) == 1:
            return _body('{"unexpected_field": 1}')
        return _body('{"event_detected": true}')

    provider._post = fake_post
    result = _call(provider)

    assert len(payloads) == 2
    assert result.execution.retry_count == 1
    assert result.execution.schema_valid is True


def test_persistent_schema_failure_is_reported_as_schema_invalid():
    provider = _provider(schema_repair_attempts=1)

    async def fake_post(client, payload, headers):
        return _body('{"unexpected_field": 1}')

    provider._post = fake_post
    with pytest.raises(LLMProviderError) as error:
        _call(provider)

    execution = error.value.execution
    assert execution.error_code == "LLM_SCHEMA_INVALID"
    assert execution.status == "FAILED"


def test_empty_completion_is_not_silently_treated_as_a_model_answer():
    provider = _provider(schema_repair_attempts=0)

    async def fake_post(client, payload, headers):
        return _body("")

    provider._post = fake_post
    with pytest.raises(LLMProviderError):
        _call(provider)


def test_thinking_budget_reserves_tokens_beyond_the_answer_budget():
    provider = _provider(deepseek_thinking_control=True, max_output_tokens=1200)
    disabled = provider._build_payload(
        model="m", messages=MESSAGES, reasoning_effort="none"
    )
    enabled = provider._build_payload(model="m", messages=MESSAGES, reasoning_effort="low")

    assert disabled["thinking"] == {"type": "disabled"}
    assert disabled["max_tokens"] == 1200
    assert enabled["thinking"] == {"type": "enabled"}
    assert enabled["max_tokens"] > 1200


def test_strip_json_text_recovers_object_from_commentary():
    assert _strip_json_text('好的，结果如下：{"a": 1} 以上。') == '{"a": 1}'


def _doc(index: int) -> NewsDocument:
    base = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)
    # Disjoint CJK code point blocks guarantee zero bigram overlap between headlines, so the
    # similarity clustering cannot merge them and the budget is what bounds the result.
    title = "".join(chr(0x4E00 + index * 8 + offset) for offset in range(8))
    return NewsDocument(
        source_id="TEST",
        source_tier=SourceTier.C,
        title=title,
        content=title,
        published_at=base + timedelta(minutes=index),
        retrieved_at=base + timedelta(minutes=index + 1),
    )


def test_cluster_budget_bounds_llm_fan_out():
    expected = min(
        int(get_settings().event_max_clusters),
        int(news_config().get("max_clusters") or 10**6),
    )
    clusters = cluster_news_documents([_doc(index) for index in range(expected + 25)])

    assert len(clusters) == expected
    # Ordering stays chronological so downstream traces remain readable.
    assert clusters == sorted(clusters, key=lambda c: (c.first_seen_at, c.canonical_title))
