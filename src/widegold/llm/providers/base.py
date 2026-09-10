from typing import Any, Protocol


class LLMProvider(Protocol):
    provider_name: str

    async def structured_call(self, *, model: str, messages: list[dict[str, str]], schema: type, reasoning_effort: str) -> Any:
        ...
