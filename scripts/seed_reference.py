from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from widegold.auth.passwords import hash_password
from widegold.db.models import (
    AssetFactorWeightModel,
    ConfigVersionModel,
    RoleModel,
    UserModel,
    UserRoleModel,
)
from widegold.db.reference_materializer import materialize_reference_tables
from widegold.db.session import db_session
from widegold.settings.app import get_settings
from widegold.settings.config import load_bootstrap_yaml
from widegold.settings.config_hash import config_content_hash, json_compatible

NOW = datetime.now(timezone.utc)
EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Every YAML-backed runtime config required by runtime_config.CONFIG_BINDINGS must be seeded.
# Bootstrap code must bypass DB-backed load_yaml(): a fresh production database has no ACTIVE
# config rows until this script creates them.
BOOTSTRAP_CONFIG_FILES: dict[str, str] = {
    "ASSET_REGISTRY": "assets.yaml",
    "FACTOR_SCHEMA": "factors.yaml",
    "WEIGHTS": "weights.yaml",
    "EVENT_RULES": "rules.yaml",
    "MODEL_ROUTING": "models.yaml",
    "DATA_DEFINITION": "factor_runtime.yaml",
    "INDICATOR_REGISTRY": "indicators.yaml",
    "FACTOR_CALCULATORS": "factor_calculators.yaml",
    "NEWS_COLLECTION": "news.yaml",
    "RESILIENCE": "resilience.yaml",
    "EVALUATION": "evaluation.yaml",
    "CALENDAR": "calendar_cn.yaml",
}


def _json_compatible(value):
    """Backward-compatible wrapper for callers of the seed helper."""
    return json_compatible(value)


def _hash(data: dict) -> str:
    return config_content_hash(data)


def _version(content: dict, default: str = "1.0.0") -> str:
    return str(content.get("version", default))


def bootstrap_config_bundle() -> dict[str, dict]:
    """Load the initial production runtime bundle directly from repository YAML files."""
    bundle = {
        config_type: load_bootstrap_yaml(filename)
        for config_type, filename in BOOTSTRAP_CONFIG_FILES.items()
    }
    bundle["SCORING_ENGINE"] = {
        "version": "1.0.0",
        "implementation": "widegold.engine",
    }
    return bundle


def _ensure_config_row(session, config_type: str, content: dict) -> None:
    """Seed one config version without changing an existing installation's ACTIVE row."""
    version = _version(content)
    existing = session.execute(
        select(ConfigVersionModel).where(
            ConfigVersionModel.config_type == config_type,
            ConfigVersionModel.version == version,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return

    active = session.execute(
        select(ConfigVersionModel).where(
            ConfigVersionModel.config_type == config_type,
            ConfigVersionModel.status == "ACTIVE",
        )
    ).scalar_one_or_none()

    normalized_content = _json_compatible(content)

    session.add(
        ConfigVersionModel(
            config_type=config_type,
            version=version,
            status="ACTIVE" if active is None else "APPROVED",
            content_hash=_hash(normalized_content),
            content_json=normalized_content,
            effective_from=EFFECTIVE_FROM,
            created_at=NOW,
            approved_at=NOW,
        )
    )


def _ensure_bootstrap_admin(session) -> None:
    """
    Create the initial administrator for session-auth deployments.

    Security behavior:
    - Requires WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD when auth_mode=session.
    - Creates the account only when it does not already exist.
    - Re-running db-seed does NOT silently reset an existing administrator password.
    - Ensures both ADMIN and USER role bindings exist.
    """
    settings = get_settings()

    if settings.auth_mode == "disabled":
        return

    username = (settings.bootstrap_admin_username or "admin").strip()
    password = settings.bootstrap_admin_password

    if not username:
        raise RuntimeError("WIDEGOLD_BOOTSTRAP_ADMIN_USERNAME must not be empty")

    if not password:
        raise RuntimeError(
            "WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD must be set when WIDEGOLD_AUTH_MODE is not disabled"
        )

    # hash_password enforces the minimum password length.
    existing = session.execute(
        select(UserModel).where(UserModel.username == username)
    ).scalar_one_or_none()

    if existing is None:
        now = datetime.now(timezone.utc)
        existing = UserModel(
            username=username,
            password_hash=hash_password(password),
            display_name="WideGold Administrator",
            email=None,
            is_active=True,
            created_at=now,
            updated_at=now,
            last_login_at=None,
        )
        session.add(existing)
        session.flush()
    else:
        # Seed is idempotent and must not reset a production password on every Compose start.
        if not existing.is_active:
            existing.is_active = True
            existing.updated_at = datetime.now(timezone.utc)
        session.flush()

    for role_code in ("ADMIN", "USER"):
        binding = session.execute(
            select(UserRoleModel).where(
                UserRoleModel.user_id == existing.user_id,
                UserRoleModel.role_code == role_code,
            )
        ).scalar_one_or_none()
        if binding is None:
            session.add(
                UserRoleModel(
                    user_id=existing.user_id,
                    role_code=role_code,
                )
            )


def _ensure_weights(session, configs: dict[str, dict]) -> None:
    weights = configs["WEIGHTS"]
    factors_cfg = configs["FACTOR_SCHEMA"]
    weight_version = _version(weights)

    existing_weight = session.execute(
        select(AssetFactorWeightModel.weight_id)
        .where(AssetFactorWeightModel.weight_version == weight_version)
        .limit(1)
    ).scalar_one_or_none()
    if existing_weight is not None:
        return

    factors = {item["id"]: item for item in factors_cfg.get("factors", [])}
    for section in ("equity_sensitivity", "gold_sensitivity"):
        for factor_id, assets in weights.get(section, {}).items():
            factor = factors.get(factor_id)
            if factor is None:
                continue
            for asset_id, sensitivity in assets.items():
                for horizon, horizon_weight in factor.get("horizons", {}).items():
                    if float(horizon_weight) <= 0:
                        continue
                    session.add(
                        AssetFactorWeightModel(
                            asset_id=asset_id,
                            factor_id=factor_id,
                            horizon=horizon,
                            sensitivity=float(sensitivity),
                            max_contribution=None,
                            weight_version=weight_version,
                            effective_from=EFFECTIVE_FROM,
                            rationale="V1 engineering prior; horizon mix is stored in factor config.",
                            created_at=NOW,
                        )
                    )


def seed() -> None:
    configs = bootstrap_config_bundle()

    with db_session() as session:
        # IAM roles must exist before user-role bindings are created.
        for role_code, name in (("ADMIN", "Administrator"), ("USER", "User")):
            if session.get(RoleModel, role_code) is None:
                session.add(RoleModel(role_code=role_code, name=name))
        session.flush()

        # Create the first real administrator account.
        _ensure_bootstrap_admin(session)
        session.flush()

        # Canonical runtime config store first. Use each YAML document's real version so the
        # VersionSnapshot reflects the actual config content (e.g. 1.1.0/1.5.0/1.6.0).
        for config_type, content in configs.items():
            _ensure_config_row(session, config_type, content)

        # Query-friendly reference materializations are derived from the exact same bundle.
        materialize_reference_tables(session, configs, now=NOW)
        session.flush()

        _ensure_weights(session, configs)


if __name__ == "__main__":
    seed()
    print("WideGold reference seed completed.")
