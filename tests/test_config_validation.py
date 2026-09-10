from widegold.services.config_validation import validate_runtime_config


def test_runtime_config_contract_is_valid():
    result = validate_runtime_config()
    assert result.valid
    assert result.factor_count == 27
    assert result.calculator_count == 27
    assert result.required_indicator_count > 0
    assert not [x for x in result.issues if x.level == "ERROR"]


def test_external_indicators_are_explicitly_visible_in_diagnostics():
    result = validate_runtime_config()
    external = {x.resource for x in result.issues if x.code == "EXTERNAL_INDICATOR_REQUIRED"}
    assert "CN_DR007" in external
    assert "CN_INDEX_EARNINGS_REV" in external
    assert "GLOBAL_CENTRAL_BANK_GOLD" in external


def test_config_validation_reports_asset_level_provider_coverage_gaps():
    from widegold.services.config_validation import validate_runtime_config
    validate_runtime_config.cache_clear()
    result = validate_runtime_config()
    gaps = [issue for issue in result.issues if issue.code == "ASSET_PROVIDER_COVERAGE_MISSING"]
    resources = {issue.resource for issue in gaps}
    assert "EQ07_VALUATION_ERP:CHINEXT" in resources
    assert "EQ07_VALUATION_ERP:STAR50" in resources
    assert result.valid is True


def test_prospective_bundle_rejects_missing_calculator_without_global_override():
    from copy import deepcopy

    from widegold.services.config_validation import (
        runtime_config_bundle,
        validate_runtime_config_bundle,
    )

    bundle = deepcopy(runtime_config_bundle())
    bundle["factor_calculators.yaml"]["factors"].pop("EQ01_CN_FUNDING_LIQUIDITY")
    result = validate_runtime_config_bundle(bundle)
    assert result.valid is False
    assert any(issue.code == "FACTOR_CALCULATOR_MISSING" for issue in result.issues)


def test_prospective_bundle_rejects_unknown_weight_asset():
    from copy import deepcopy

    from widegold.services.config_validation import (
        runtime_config_bundle,
        validate_runtime_config_bundle,
    )

    bundle = deepcopy(runtime_config_bundle())
    bundle["weights.yaml"]["equity_sensitivity"]["EQ01_CN_FUNDING_LIQUIDITY"][
        "NOT_AN_ASSET"
    ] = 1.0
    result = validate_runtime_config_bundle(bundle)
    assert result.valid is False
    assert any(issue.code == "WEIGHT_ASSET_UNKNOWN" for issue in result.issues)


def test_prospective_bundle_rejects_unknown_indicator_provider():
    from copy import deepcopy

    from widegold.services.config_validation import runtime_config_bundle, validate_runtime_config_bundle

    bundle = deepcopy(runtime_config_bundle())
    target = next(row for row in bundle["indicators.yaml"]["indicators"] if row["id"] == "CN_DR007")
    target["provider"] = "typo_provider"
    result = validate_runtime_config_bundle(bundle)
    assert result.valid is False
    assert any(issue.code == "INDICATOR_PROVIDER_UNKNOWN" for issue in result.issues)


def test_prospective_bundle_rejects_incomplete_external_contract():
    from copy import deepcopy

    from widegold.services.config_validation import runtime_config_bundle, validate_runtime_config_bundle

    bundle = deepcopy(runtime_config_bundle())
    target = next(row for row in bundle["indicators.yaml"]["indicators"] if row["id"] == "CN_DR007")
    target["external_params"].pop("required_metadata")
    result = validate_runtime_config_bundle(bundle)
    assert result.valid is False
    assert any(issue.code == "EXTERNAL_CONTRACT_INCOMPLETE" for issue in result.issues)
