from dataclasses import dataclass

from widegold.settings.config import load_yaml


@dataclass(frozen=True)
class ModelAlias:
    alias: str
    tier: str
    provider: str
    model: str
    reasoning_effort: str


class ModelRouter:
    """Resolve task types to ordered model aliases for production and fallback routing."""

    def __init__(self) -> None:
        cfg = load_yaml("models.yaml")
        self.aliases = {
            alias: ModelAlias(alias=alias, **definition)
            for alias, definition in cfg["aliases"].items()
        }
        self.routes = cfg["routes"]

    def route(self, task_type: str) -> list[ModelAlias]:
        return [self.aliases[name] for name in self.routes[task_type]]
