"""Probe the real LLM routing chain with one tiny structured call per alias.

This is the cheap replacement for ``docker-e2e.ps1 -Analysis`` when the question is only
"does the model contract work at all?".  It answers, per alias:

* which model string the router actually resolved (config drift shows up here immediately);
* whether the provider is configured at all in this environment;
* whether the upstream API accepts the request body (model name, thinking/reasoning params);
* whether the response validates against the production Pydantic schema.

Usage (inside the api container):

    docker compose exec api python scripts/llm_probe.py
    docker compose exec api python scripts/llm_probe.py --task reasoning

Cost: one short call per configured alias of the selected task type.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from widegold.domain.enums import ModelTier
from widegold.llm.providers.http_compatible import LLMProviderError
from widegold.llm.service import LLMService
from widegold.schemas.events import EventExtractionOutput

SAMPLE_PAYLOAD = {
    "canonical_title": "央行开展大额逆回购操作，银行间资金面转松",
    "documents": [
        {
            "title": "央行开展大额逆回购操作，银行间资金面转松",
            "content": "人民银行今日开展逆回购操作，规模高于当日到期量，银行间市场隔夜与七天回购利率均有回落。",
            "source_tier": "C",
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
    ],
}

SYSTEM_PROMPT = "你是金融事件抽取器。只抽取提供材料中的事实，不补充未知信息。输出必须符合JSON schema。"


async def probe(task_type: str) -> int:
    service = LLMService()
    try:
        aliases = service.router.route(task_type)
    except KeyError:
        print(f"Unknown task type: {task_type}")
        return 2

    print(f"task_type={task_type}")
    print(f"routed aliases: {[alias.alias for alias in aliases]}\n")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(SAMPLE_PAYLOAD, ensure_ascii=False)},
    ]

    successes = 0
    for alias in aliases:
        header = f"{alias.alias} -> provider={alias.provider} model={alias.model} effort={alias.reasoning_effort}"
        try:
            provider = service._provider(alias)  # noqa: SLF001 - diagnostic tool, by design
        except RuntimeError as exc:
            print(f"[skip] {header}\n       not configured: {exc}\n")
            continue
        try:
            result = await provider.structured_call(
                model_alias=alias.alias,
                model=alias.model,
                tier=ModelTier(alias.tier),
                task_type=task_type,
                messages=messages,
                schema=EventExtractionOutput,
                reasoning_effort=alias.reasoning_effort,
            )
        except LLMProviderError as exc:
            execution = exc.execution
            print(
                f"[fail] {header}\n"
                f"       error_code={execution.error_code} retries={execution.retry_count}\n"
                f"       {str(exc)[:600]}\n"
            )
            continue
        execution = result.execution
        successes += 1
        print(
            f"[ok]   {header}\n"
            f"       latency={execution.latency_ms}ms in={execution.input_tokens} "
            f"out={execution.output_tokens} retries={execution.retry_count}\n"
            f"       {result.data.model_dump_json()[:400]}\n"
        )

    if successes == 0:
        print("No alias produced a valid structured output: the event pipeline cannot emit events.")
        return 1
    print(f"{successes}/{len(aliases)} alias(es) returned a schema-valid response.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default="extraction", help="task type to probe (default: extraction)")
    args = parser.parse_args()
    return asyncio.run(probe(args.task))


if __name__ == "__main__":
    sys.exit(main())
