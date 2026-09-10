from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "configs"
FIXTURE_DIR = REPO_ROOT / "fixtures"


@lru_cache(maxsize=32)
def _load_yaml_static(name: str) -> dict[str, Any]:
    with (CONFIG_DIR / name).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_bootstrap_yaml(name: str) -> dict[str, Any]:
    """Read the repository YAML directly, bypassing runtime DB configuration.

    Docker bootstrap/seed must use this path because a fresh PostgreSQL database has no ACTIVE
    config rows yet. Runtime application code should continue to use :func:`load_yaml`.
    """
    return _load_yaml_static(name)


def load_yaml(name: str) -> dict[str, Any]:
    from widegold.settings.runtime_config import runtime_config_content
    runtime = runtime_config_content(name)
    return runtime if runtime is not None else _load_yaml_static(name)


def asset_config() -> dict[str, Any]:
    return load_yaml("assets.yaml")


def factor_config() -> dict[str, Any]:
    return load_yaml("factors.yaml")


def weight_config() -> dict[str, Any]:
    return load_yaml("weights.yaml")


def rule_config() -> dict[str, Any]:
    return load_yaml("rules.yaml")


def resilience_config() -> dict[str, Any]:
    return load_yaml("resilience.yaml")


def factor_runtime_config() -> dict[str, Any]:
    return load_yaml("factor_runtime.yaml")


def cn_calendar_config() -> dict[str, Any]:
    return load_yaml("calendar_cn.yaml")


def indicator_config() -> dict[str, Any]:
    return load_yaml("indicators.yaml")


def factor_calculator_config() -> dict[str, Any]:
    return load_yaml("factor_calculators.yaml")


def _version_of(config: dict[str, Any], default: str = "1.0.0") -> str:
    return str(config.get("version", default))


def current_version_snapshot():
    # Local imports avoid settings -> schema/runtime initialization cycles.
    from widegold.schemas.common import VersionSnapshot
    from widegold.settings.runtime_config import active_runtime_version_map

    active = active_runtime_version_map()
    if active is not None:
        # Prompts are not yet a standalone V1 runtime config file. Keep their version explicit
        # while every YAML-backed runtime config and the scoring engine come from one DB snapshot.
        return VersionSnapshot(prompts="1.0.0", **active)

    return VersionSnapshot(
        factor_schema=_version_of(factor_config()),
        factor_logic=_version_of(factor_runtime_config()),
        weights=_version_of(weight_config()),
        event_rules=_version_of(rule_config()),
        prompts=_version_of(load_yaml("prompts.yaml")) if (CONFIG_DIR / "prompts.yaml").exists() else "1.0.0",
        scoring=_version_of(load_yaml("scoring.yaml")) if (CONFIG_DIR / "scoring.yaml").exists() else "1.0.0",
        data_definition=_version_of(factor_runtime_config()),
        model_routing=_version_of(load_yaml("models.yaml")),
        indicator_registry=_version_of(indicator_config()),
        factor_calculators=_version_of(factor_calculator_config()),
        news_collection=_version_of(news_config()),
        assets=_version_of(asset_config()),
        resilience=_version_of(resilience_config()),
        evaluation=_version_of(evaluation_config()),
        calendar=_version_of(cn_calendar_config()),
    )


def news_config() -> dict[str, Any]:
    return load_yaml("news.yaml")


def evaluation_config() -> dict[str, Any]:
    return load_yaml("evaluation.yaml")
