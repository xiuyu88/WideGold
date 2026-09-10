from __future__ import annotations

from widegold.repositories.factory import repository
from widegold.schemas.indicators import IndicatorObservation
from widegold.settings.config import asset_config, indicator_config


def _validate_indicator_contract(item: IndicatorObservation, spec: dict) -> list[str]:
    errors: list[str] = []
    params = spec.get("external_params") or spec.get("validation_params") or {}
    asset_scoped = bool(params.get("asset_scoped", False))
    valid_assets = {row["id"] for row in asset_config().get("assets", []) if row.get("active", True)}

    if asset_scoped and not item.asset_id:
        errors.append("asset_id is required for this external indicator")
    if not asset_scoped and item.asset_id is not None:
        errors.append("asset_id must be null for this global external indicator")
    if item.asset_id is not None and item.asset_id not in valid_assets:
        errors.append(f"unknown asset_id {item.asset_id}")

    expected_range = params.get("expected_range")
    if isinstance(expected_range, list) and len(expected_range) == 2:
        lo, hi = float(expected_range[0]), float(expected_range[1])
        if not lo <= float(item.value) <= hi:
            errors.append(
                f"value {item.value} outside expected range [{lo}, {hi}] "
                f"for unit {params.get('unit', 'unspecified')}"
            )

    if item.release_ts > item.ingest_ts:
        errors.append("release_ts cannot be later than ingest_ts")

    required_metadata = params.get("required_metadata") or []
    for key in required_metadata:
        value = item.metadata.get(str(key))
        if value is None or value == "" or value == []:
            errors.append(f"metadata.{key} is required")

    for key, expected in (params.get("metadata_equals") or {}).items():
        actual = item.metadata.get(key)
        if actual != expected:
            errors.append(f"metadata.{key} must equal {expected!r}")

    for key, allowed in (params.get("metadata_allowed_values") or {}).items():
        actual = item.metadata.get(key)
        if actual is not None and actual not in allowed:
            errors.append(f"metadata.{key} must be one of {allowed!r}")

    for key, forbidden in (params.get("metadata_forbidden_values") or {}).items():
        actual = item.metadata.get(key)
        if actual is not None and actual in forbidden:
            errors.append(f"metadata.{key} must not be one of {forbidden!r}")

    for key, minimum in (params.get("metadata_numeric_min") or {}).items():
        actual = item.metadata.get(key)
        if actual is None:
            continue
        try:
            numeric = float(actual)
        except (TypeError, ValueError):
            errors.append(f"metadata.{key} must be numeric")
        else:
            if numeric < float(minimum):
                errors.append(f"metadata.{key} must be >= {minimum}")

    return errors


def validate_indicator_observations(
    items: list[IndicatorObservation], *, specs: dict[str, dict] | None = None
) -> dict[str, dict]:
    """Validate normalized observations before they enter point-in-time storage.

    The function is intentionally repository-free so both machine PUSH ingestion and provider/
    bridge PULL collection must cross the same data-contract boundary.
    """
    specs = specs or {row["id"]: row for row in indicator_config()["indicators"]}
    invalid = sorted({item.indicator_id for item in items if item.indicator_id not in specs})
    if invalid:
        raise ValueError(f"Unknown indicator_id(s): {','.join(invalid)}")

    contract_errors: list[str] = []
    for item in items:
        spec = specs[item.indicator_id]
        if spec.get("provider") == "external" or spec.get("validation_params"):
            errors = _validate_indicator_contract(item, spec)
            contract_errors.extend(
                f"{item.indicator_id}{'::' + item.asset_id if item.asset_id else ''}: {error}"
                for error in errors
            )
    if contract_errors:
        raise ValueError("External indicator contract violation(s): " + "; ".join(contract_errors))
    return specs


def ingest_indicator_observations(items: list[IndicatorObservation]) -> dict:
    specs = validate_indicator_observations(items)
    repository().save_indicator_observations(items)
    return {
        "accepted": len(items),
        "indicator_ids": sorted({item.indicator_id for item in items}),
        "validated_external_contracts": sum(1 for item in items if specs[item.indicator_id].get("provider") == "external"),
    }
