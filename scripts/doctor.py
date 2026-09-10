from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _dotenv_value(name: str) -> str | None:
    """Read one simple KEY=VALUE entry from the project .env without extra dependencies."""
    path = Path(".env")
    if not path.exists():
        return None
    try:
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() != name:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            return value
    except OSError:
        return None
    return None


def _default_api_url() -> str:
    explicit = os.getenv("WIDEGOLD_DOCTOR_API_URL")
    if explicit:
        return explicit

    port = os.getenv("HTTP_PORT") or _dotenv_value("HTTP_PORT") or "80"
    return "http://localhost" if port == "80" else f"http://localhost:{port}"


def _default_prefect_url() -> str:
    explicit = os.getenv("WIDEGOLD_DOCTOR_PREFECT_URL")
    if explicit:
        return explicit

    port = os.getenv("PREFECT_PORT") or _dotenv_value("PREFECT_PORT") or "4200"
    return f"http://localhost:{port}/api/health"


def fetch_json(url: str) -> tuple[bool, dict | str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return True, json.load(response)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return False, str(exc)


def main() -> int:
    base = _default_api_url()
    prefect_url = _default_prefect_url()
    external_health_url = os.getenv("WIDEGOLD_DOCTOR_EXTERNAL_HEALTH_URL")

    health_ok, health = fetch_json(f"{base.rstrip('/')}/health")
    ready_ok, ready = fetch_json(f"{base.rstrip('/')}/ready")

    print("WideGold Doctor")
    print("===============")
    print(f"API base     : {base}")
    print(f"API /health : {'OK' if health_ok else 'FAIL'}")
    if health_ok:
        print(f"  service={health.get('service')} data_mode={health.get('data_mode')}")
    else:
        print(f"  {health}")

    print(f"API /ready  : {'OK' if ready_ok else 'FAIL'}")
    if ready_ok:
        print(f"  ready={ready.get('ready')} degraded={ready.get('degraded')}")
        for check in ready.get("checks", []):
            required = "required" if check.get("required") else "optional"
            print(
                f"  - {check.get('name')}: {check.get('status')} "
                f"({required}, {check.get('latency_ms')} ms)"
            )
            if check.get("message"):
                print(f"      {check['message']}")
        cfg = ready.get("config") or {}
        print(
            "  config factors={}/{} indicators={} external={}".format(
                cfg.get("factor_count"),
                cfg.get("calculator_count"),
                cfg.get("required_indicator_count"),
                cfg.get("external_indicator_count"),
            )
        )
    else:
        print(f"  {ready}")

    prefect_ok = True
    orchestration = (
        str(health.get("orchestration", ""))
        if health_ok and isinstance(health, dict)
        else ""
    )
    if orchestration.lower() == "prefect":
        prefect_ok, prefect = fetch_json(prefect_url)
        print(f"Prefect API : {'OK' if prefect_ok else 'FAIL'}")
        if not prefect_ok:
            print(f"  {prefect}")
    else:
        print("Prefect API : SKIPPED (orchestration is not prefect)")

    external_ok = True
    if external_health_url:
        external_ok, external = fetch_json(external_health_url)
        print(f"External    : {'OK' if external_ok else 'FAIL'}")
        if not external_ok:
            print(f"  {external}")
    else:
        print(
            "External    : SKIPPED "
            "(set WIDEGOLD_DOCTOR_EXTERNAL_HEALTH_URL to probe a bridge)"
        )

    app_ready = bool(ready.get("ready")) if ready_ok and isinstance(ready, dict) else False
    return 0 if health_ok and ready_ok and app_ready and prefect_ok and external_ok else 2


if __name__ == "__main__":
    sys.exit(main())
