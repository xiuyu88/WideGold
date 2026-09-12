"""Inspect and align DB runtime config with the repository YAML bundle.

Why this exists
---------------
``db-seed`` deliberately never steals the ACTIVE row from an existing installation: a newly seeded
version lands as APPROVED whenever some version of that config type is already ACTIVE.  Editing
``configs/*.yaml`` and restarting Docker therefore changes *nothing* at runtime until someone
activates the new version explicitly.  A silent drift between "the YAML in Git" and "what the
worker actually loaded" is very hard to see from logs alone, so this CLI prints both and can close
the gap.

Usage (inside the api container, which already has DB credentials):

    docker compose exec api python scripts/sync_runtime_config.py --check
    docker compose exec api python scripts/sync_runtime_config.py --activate MODEL_ROUTING NEWS_COLLECTION
    docker compose exec api python scripts/sync_runtime_config.py --activate-all

``--check`` is read-only and safe to run at any time, including against a live installation.
"""

from __future__ import annotations

import argparse
import sys

from widegold.repositories.factory import repository
from widegold.services.runtime_config_admin import (
    activate_runtime_config_version,
    stage_runtime_config_version,
)
from widegold.settings.config import load_bootstrap_yaml
from widegold.settings.runtime_config import CONFIG_BINDINGS

# config_type -> configs/*.yaml filename, derived from the runtime bindings so this script can
# never drift away from what the application actually loads.
CONFIG_TYPE_TO_FILE = {binding.config_type: name for name, binding in CONFIG_BINDINGS.items()}


def _yaml_version(filename: str) -> str:
    return str(load_bootstrap_yaml(filename).get("version", "1.0.0"))


def _active_version(config_type: str) -> str | None:
    for row in repository().list_config_versions(config_type=config_type):
        if row.get("status") == "ACTIVE":
            return str(row.get("version"))
    return None


def check() -> int:
    """Print YAML version vs ACTIVE DB version; return non-zero when they diverge."""
    drift = 0
    print(f"{'CONFIG_TYPE':<22}{'YAML':<12}{'ACTIVE(DB)':<14}STATE")
    for config_type, filename in sorted(CONFIG_TYPE_TO_FILE.items()):
        yaml_version = _yaml_version(filename)
        active = _active_version(config_type)
        if active is None:
            state = "NO ACTIVE ROW"
            drift += 1
        elif active == yaml_version:
            state = "ok"
        else:
            state = "DRIFT -> yaml version is not the one in use"
            drift += 1
        print(f"{config_type:<22}{yaml_version:<12}{str(active):<14}{state}")
    if drift:
        print(
            f"\n{drift} config type(s) are not running the repository version. "
            "Re-run with --activate <TYPE> ... or --activate-all."
        )
    return 1 if drift else 0


def activate(config_types: list[str]) -> int:
    failures = 0
    for config_type in config_types:
        filename = CONFIG_TYPE_TO_FILE.get(config_type)
        if filename is None:
            print(f"[skip] {config_type}: not a runtime-activatable config type")
            failures += 1
            continue
        content = load_bootstrap_yaml(filename)
        version = str(content.get("version", "1.0.0"))
        try:
            # Staging is idempotent: an identical (type, version) row is reused, and a row with the
            # same version but different content is rejected rather than silently rewritten.
            stage_runtime_config_version(
                config_type, version, content, status="APPROVED", actor_user_id=None
            )
        except ValueError as exc:
            message = str(exc)
            if "already exists" not in message:
                print(f"[fail] {config_type} {version}: {message}")
                failures += 1
                continue
            print(f"[fail] {config_type} {version}: {message}")
            print("       Bump the version field in the YAML file and re-run.")
            failures += 1
            continue
        try:
            result = activate_runtime_config_version(config_type, version, actor_user_id=None)
        except (LookupError, ValueError) as exc:
            print(f"[fail] {config_type} {version}: {exc}")
            failures += 1
            continue
        print(
            f"[ok]   {config_type} {version} is now ACTIVE "
            f"(runtime_config_generation={result.get('runtime_config_generation')})"
        )
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report YAML vs ACTIVE DB versions")
    parser.add_argument("--activate", nargs="*", metavar="CONFIG_TYPE", help="activate these types")
    parser.add_argument("--activate-all", action="store_true", help="activate every config type")
    args = parser.parse_args()

    if args.activate_all:
        return activate(sorted(CONFIG_TYPE_TO_FILE))
    if args.activate:
        return activate([item.upper() for item in args.activate])
    return check()


if __name__ == "__main__":
    sys.exit(main())
