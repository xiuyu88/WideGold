from __future__ import annotations

from functools import lru_cache
from typing import Mapping

from widegold.schemas.system import ConfigIssue, ConfigValidationResult
from widegold.settings.config import load_yaml


RUNTIME_FILENAMES = (
    "assets.yaml",
    "factors.yaml",
    "weights.yaml",
    "rules.yaml",
    "models.yaml",
    "factor_runtime.yaml",
    "indicators.yaml",
    "factor_calculators.yaml",
    "news.yaml",
    "resilience.yaml",
    "evaluation.yaml",
    "calendar_cn.yaml",
)


def runtime_config_bundle() -> dict[str, dict]:
    """Load the currently effective runtime config bundle."""
    return {name: load_yaml(name) for name in RUNTIME_FILENAMES}


def validate_runtime_config_bundle(bundle: Mapping[str, dict]) -> ConfigValidationResult:
    """Validate a complete prospective runtime config bundle without mutating global state.

    This pure-ish entry point is used both by readiness checks and inside the PostgreSQL config
    activation transaction.  It lets an APPROVED candidate be checked together with all other
    currently ACTIVE configs *before* the database commits the activation.
    """
    issues: list[ConfigIssue] = []
    factors_cfg = bundle["factors.yaml"]
    calculators_cfg = bundle["factor_calculators.yaml"]
    indicators_cfg = bundle["indicators.yaml"]
    assets_cfg = bundle["assets.yaml"]
    model_cfg = bundle["models.yaml"]
    weights = bundle["weights.yaml"]

    factors = factors_cfg.get("factors", [])
    factor_ids = {row["id"] for row in factors}
    calculators = calculators_cfg.get("factors", {})
    indicators = indicators_cfg.get("indicators", [])
    indicator_ids = {row["id"] for row in indicators}
    assets = assets_cfg.get("assets", [])
    asset_ids = {row["id"] for row in assets}
    equity_assets = {row["id"] for row in assets if row.get("family") == "equity"}

    if len(factor_ids) != 27:
        issues.append(
            ConfigIssue(
                level="ERROR",
                code="FACTOR_COUNT_MISMATCH",
                message=f"V1 expects 27 logical factors; found {len(factor_ids)}.",
                resource="factors.yaml",
            )
        )

    missing_calculators = sorted(factor_ids - set(calculators))
    extra_calculators = sorted(set(calculators) - factor_ids)
    for factor_id in missing_calculators:
        issues.append(
            ConfigIssue(
                level="ERROR",
                code="FACTOR_CALCULATOR_MISSING",
                message="Factor has no calculator.",
                resource=factor_id,
            )
        )
    for factor_id in extra_calculators:
        issues.append(
            ConfigIssue(
                level="ERROR",
                code="FACTOR_CALCULATOR_UNKNOWN",
                message="Calculator references unknown factor.",
                resource=factor_id,
            )
        )

    required_indicators: set[str] = set()
    for factor_id, spec in calculators.items():
        if factor_id not in factor_ids:
            continue
        scope = spec.get("scope")
        indicator_map = spec.get("asset_indicator_map", {})
        if scope == "asset" and any(
            row.get("indicator") == "$ASSET" for row in spec.get("inputs", [])
        ):
            missing_assets = sorted(equity_assets - set(indicator_map))
            for asset_id in missing_assets:
                issues.append(
                    ConfigIssue(
                        level="ERROR",
                        code="ASSET_INDICATOR_MAP_MISSING",
                        message=f"Asset-scoped factor has no indicator mapping for {asset_id}.",
                        resource=factor_id,
                    )
                )
        for row in spec.get("inputs", []):
            indicator = row.get("indicator")
            if indicator and indicator != "$ASSET":
                required_indicators.add(indicator)
        required_indicators.update(str(v) for v in indicator_map.values() if v)

    for indicator_id in sorted(required_indicators - indicator_ids):
        issues.append(
            ConfigIssue(
                level="ERROR",
                code="INDICATOR_DEFINITION_MISSING",
                message="Calculator references an indicator not present in indicators.yaml.",
                resource=indicator_id,
            )
        )

    indicator_by_id = {row["id"]: row for row in indicators}

    # A logically valid asset-scoped calculator can intentionally have provider gaps. Surface
    # those as warnings instead of silently substituting a semantically different market proxy.
    for factor_id, spec in calculators.items():
        if spec.get("scope") != "asset" or factor_id not in factor_ids:
            continue
        family = next((row.get("family") for row in factors if row["id"] == factor_id), None)
        family_assets = {row["id"] for row in assets if row.get("family") == family}
        for input_spec in spec.get("inputs", []):
            indicator_id = input_spec.get("indicator")
            if (
                not input_spec.get("asset_scoped")
                or not indicator_id
                or indicator_id == "$ASSET"
            ):
                continue
            indicator_spec = indicator_by_id.get(indicator_id, {})
            asset_params = (indicator_spec.get("params") or {}).get("asset_params")
            if isinstance(asset_params, dict):
                for asset_id in sorted(family_assets - set(asset_params)):
                    issues.append(
                        ConfigIssue(
                            level="WARNING",
                            code="ASSET_PROVIDER_COVERAGE_MISSING",
                            message=(
                                f"Provider for {indicator_id} has no asset parameters for "
                                f"{asset_id}; this scoped factor may be unavailable."
                            ),
                            resource=f"{factor_id}:{asset_id}",
                        )
                    )

    aliases = model_cfg.get("aliases", {})
    allowed_providers = {"qwen", "deepseek", "openai_compatible"}
    for alias, definition in aliases.items():
        if definition.get("provider") not in allowed_providers:
            issues.append(
                ConfigIssue(
                    level="ERROR",
                    code="MODEL_PROVIDER_UNKNOWN",
                    message=(
                        "Model alias uses unsupported provider: "
                        f"{definition.get('provider')}"
                    ),
                    resource=alias,
                )
            )
        if not definition.get("model"):
            issues.append(
                ConfigIssue(
                    level="ERROR",
                    code="MODEL_NAME_MISSING",
                    message="Model alias must declare a concrete model name.",
                    resource=alias,
                )
            )
    for route_name, route_aliases in model_cfg.get("routes", {}).items():
        for alias in route_aliases:
            if alias not in aliases:
                issues.append(
                    ConfigIssue(
                        level="ERROR",
                        code="MODEL_ROUTE_ALIAS_UNKNOWN",
                        message=f"Route {route_name} references unknown alias {alias}.",
                        resource=route_name,
                    )
                )

    allowed_indicator_providers = {"fred", "akshare", "akshare_derived", "nbs", "external"}
    for indicator_id in sorted(required_indicators & indicator_ids):
        spec = indicator_by_id[indicator_id]
        provider = spec.get("provider", "external")
        if provider not in allowed_indicator_providers:
            issues.append(
                ConfigIssue(
                    level="ERROR",
                    code="INDICATOR_PROVIDER_UNKNOWN",
                    message=f"Indicator uses unsupported provider: {provider}",
                    resource=indicator_id,
                )
            )
        if provider == "akshare" and not spec.get("parser"):
            issues.append(
                ConfigIssue(
                    level="ERROR",
                    code="INDICATOR_PARSER_MISSING",
                    message="AKShare indicator requires a parser definition.",
                    resource=indicator_id,
                )
            )
        if provider == "nbs":
            params = spec.get("params") or {}
            validation_params = spec.get("validation_params") or {}
            if not params.get("listing_url"):
                issues.append(
                    ConfigIssue(
                        level="ERROR",
                        code="NBS_LISTING_URL_MISSING",
                        message="NBS indicator must declare params.listing_url.",
                        resource=indicator_id,
                    )
                )
            if not validation_params.get("required_metadata"):
                issues.append(
                    ConfigIssue(
                        level="ERROR",
                        code="NBS_VALIDATION_CONTRACT_MISSING",
                        message="NBS indicator must declare validation_params.required_metadata.",
                        resource=indicator_id,
                    )
                )
        if provider == "external":
            external_params = spec.get("external_params") or {}
            capability = external_params.get("capability")
            if not capability:
                issues.append(
                    ConfigIssue(
                        level="ERROR",
                        code="EXTERNAL_CAPABILITY_MISSING",
                        message=(
                            "External indicator must declare external_params.capability."
                        ),
                        resource=indicator_id,
                    )
                )
            for field in ("unit", "semantic", "asset_scoped", "required_metadata"):
                if field not in external_params:
                    issues.append(
                        ConfigIssue(
                            level="ERROR",
                            code="EXTERNAL_CONTRACT_INCOMPLETE",
                            message=f"External indicator must declare external_params.{field}.",
                            resource=indicator_id,
                        )
                    )
            issues.append(
                ConfigIssue(
                    level="WARNING",
                    code="EXTERNAL_INDICATOR_REQUIRED",
                    message=(
                        "Indicator depends on MCP/manual/external ingestion and may be "
                        "unavailable."
                    ),
                    resource=indicator_id,
                )
            )

    for section in ("equity_sensitivity", "gold_sensitivity"):
        for factor_id, mapping in weights.get(section, {}).items():
            if factor_id not in factor_ids:
                issues.append(
                    ConfigIssue(
                        level="ERROR",
                        code="WEIGHT_FACTOR_UNKNOWN",
                        message="Weight references unknown factor.",
                        resource=factor_id,
                    )
                )
            for asset_id in mapping:
                if asset_id not in asset_ids:
                    issues.append(
                        ConfigIssue(
                            level="ERROR",
                            code="WEIGHT_ASSET_UNKNOWN",
                            message="Weight references unknown asset.",
                            resource=asset_id,
                        )
                    )

    external_count = sum(
        1
        for indicator_id in required_indicators
        if indicator_by_id.get(indicator_id, {}).get("provider") == "external"
    )
    return ConfigValidationResult(
        valid=not any(issue.level == "ERROR" for issue in issues),
        factor_count=len(factor_ids),
        calculator_count=len(calculators),
        indicator_count=len(indicator_ids),
        required_indicator_count=len(required_indicators),
        external_indicator_count=external_count,
        issues=issues,
    )


@lru_cache(maxsize=1)
def validate_runtime_config() -> ConfigValidationResult:
    return validate_runtime_config_bundle(runtime_config_bundle())
