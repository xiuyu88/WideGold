from __future__ import annotations

from uuid import UUID

from widegold.repositories.factory import repository
from widegold.runtime.config_epoch import bump_runtime_config_generation
from widegold.services.config_validation import (
    runtime_config_bundle,
    validate_runtime_config,
    validate_runtime_config_bundle,
)
from widegold.settings.runtime_config import CONFIG_BINDINGS, clear_runtime_config_cache

ACTIVATABLE_CONFIG_TYPES = {binding.config_type for binding in CONFIG_BINDINGS.values()}
CONFIG_TYPE_TO_FILENAME = {binding.config_type: filename for filename, binding in CONFIG_BINDINGS.items()}


def list_runtime_config_versions(config_type: str | None = None) -> list[dict]:
    if config_type is not None:
        config_type = config_type.upper()
    return repository().list_config_versions(config_type=config_type)


def get_runtime_config_version(config_type: str, version: str) -> dict | None:
    return repository().get_config_version(config_type.upper(), version)


def stage_runtime_config_version(
    config_type: str,
    version: str,
    content: dict,
    *,
    status: str,
    actor_user_id: UUID | None,
) -> dict:
    config_type = config_type.upper()
    status = status.upper()
    if config_type not in ACTIVATABLE_CONFIG_TYPES:
        raise ValueError(f"Unsupported runtime config type: {config_type}")
    if status not in {"DRAFT", "APPROVED"}:
        raise ValueError("New runtime config versions must be DRAFT or APPROVED")
    declared_version = str(content.get("version", ""))
    if declared_version != version:
        raise ValueError(
            f"Config content version ({declared_version or 'missing'}) must equal requested version ({version})"
        )

    bundle = runtime_config_bundle()
    bundle[CONFIG_TYPE_TO_FILENAME[config_type]] = content
    validation = validate_runtime_config_bundle(bundle)
    if status == "APPROVED" and not validation.valid:
        errors = [
            f"{issue.code}:{issue.resource or '-'}"
            for issue in validation.issues
            if issue.level == "ERROR"
        ]
        raise ValueError(
            "APPROVED config candidate failed cross-config validation: "
            + ", ".join(errors[:12])
        )

    row, created = repository().create_config_version(
        config_type,
        version,
        content,
        status=status,
        actor_user_id=actor_user_id,
    )
    return {
        **row,
        "created": created,
        "validation": validation.model_dump(mode="json"),
    }


def activate_runtime_config_version(
    config_type: str,
    version: str,
    *,
    actor_user_id: UUID | None,
) -> dict:
    config_type = config_type.upper()
    if config_type not in ACTIVATABLE_CONFIG_TYPES:
        raise ValueError(
            f"Config type {config_type} is not runtime-activatable in V1. "
            "Only versioned YAML-backed runtime configs may be activated."
        )

    result = repository().activate_config_version(
        config_type, version, actor_user_id=actor_user_id
    )
    if result is None:
        raise LookupError(f"Config version not found: {config_type} {version}")

    # Local process updates immediately. Other API/Prefect processes observe the Redis generation
    # bump and clear their short-lived local caches on the next config access.
    clear_runtime_config_cache()
    validate_runtime_config.cache_clear()
    generation = bump_runtime_config_generation()
    return {**result, "runtime_config_generation": generation}
