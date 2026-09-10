"""Production-shaped WideGold smoke test.

Run from the Docker host:
    python scripts/e2e_smoke.py

Or from the optional Compose tools profile:
    docker compose --profile tools run --rm smoke

The smoke test never publishes a manual analysis. If analysis triggering is enabled, it requests
PREVIEW_ONLY and accepts PREVIEW_READY or QUALITY_FAILED as valid terminal outcomes. This makes it
safe to run while real providers are partially configured.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any


@dataclass
class Step:
    name: str
    ok: bool
    detail: str
    required: bool = True


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, method: str, path: str, payload: dict | None = None, timeout: float = 8.0) -> tuple[int, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method, headers=headers
        )
        try:
            with self.opener.open(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return int(response.status), json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw
            return int(exc.code), body


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _detail(value: Any, limit: int = 240) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 3] + "..."


def main() -> int:
    base = os.getenv("WIDEGOLD_SMOKE_API_URL", "http://localhost")
    prefect = os.getenv("WIDEGOLD_SMOKE_PREFECT_URL", "http://localhost:4200/api/health")
    username = os.getenv("WIDEGOLD_SMOKE_ADMIN_USERNAME") or os.getenv("WIDEGOLD_BOOTSTRAP_ADMIN_USERNAME", "admin")
    password = os.getenv("WIDEGOLD_SMOKE_ADMIN_PASSWORD") or os.getenv("WIDEGOLD_BOOTSTRAP_ADMIN_PASSWORD")
    trigger = _bool_env("WIDEGOLD_SMOKE_TRIGGER_ANALYSIS", False)
    max_poll_seconds = int(os.getenv("WIDEGOLD_SMOKE_MAX_POLL_SECONDS", "600"))
    existing_run_id = (os.getenv("WIDEGOLD_SMOKE_RUN_ID") or "").strip() or None

    client = Client(base)
    steps: list[Step] = []

    status, health = client.request("GET", "/health")
    steps.append(Step("API health", status == 200, f"HTTP {status} {_detail(health)}"))
    if status != 200:
        return _finish(steps)

    status, ready = client.request("GET", "/ready")
    app_ready = status == 200 and bool((ready or {}).get("ready"))
    steps.append(Step("API readiness", app_ready, f"HTTP {status} {_detail(ready)}"))

    # Prefect is operationally important but the API may still serve the last Published snapshot
    # if orchestration is temporarily down, so report it as optional here.
    try:
        with urllib.request.urlopen(prefect, timeout=5) as response:
            prefect_ok = 200 <= int(response.status) < 300
            prefect_detail = f"HTTP {response.status}"
    except Exception as exc:  # noqa: BLE001 - smoke runner should report, not crash
        prefect_ok = False
        prefect_detail = str(exc)
    steps.append(Step("Prefect health", prefect_ok, prefect_detail, required=False))

    auth_mode = str((health or {}).get("auth_mode", ""))
    # Older /health payloads may not expose auth_mode. Attempt login only when credentials exist.
    if password:
        status, principal = client.request(
            "POST", "/api/v1/auth/login", {"username": username, "password": password}
        )
        login_ok = status == 200 and "ADMIN" in set((principal or {}).get("roles", []))
        steps.append(Step("Admin login", login_ok, f"HTTP {status} {_detail(principal)}"))
        if not login_ok:
            return _finish(steps)
    else:
        steps.append(
            Step(
                "Admin login",
                auth_mode in {"disabled", ""},
                "No smoke/admin password configured; admin-only checks skipped",
                required=auth_mode not in {"disabled", ""},
            )
        )

    # Admin diagnostics verifies runtime config versioning, repository connectivity and external
    # indicator observability through one application-level contract.
    status, diagnostics = client.request("GET", "/api/v1/admin/system/diagnostics")
    diag_ok = status == 200 and bool((diagnostics or {}).get("config", {}).get("valid"))
    steps.append(Step("Admin diagnostics", diag_ok, f"HTTP {status} {_detail(diagnostics)}"))

    status, versions = client.request("GET", "/api/v1/admin/config/versions")
    items = (versions or {}).get("items", []) if isinstance(versions, dict) else []
    active_types = {row.get("config_type") for row in items if row.get("status") == "ACTIVE"}
    config_ok = status == 200 and len(active_types) >= 8
    steps.append(
        Step(
            "Runtime config ACTIVE set",
            config_ok,
            f"HTTP {status}; active_types={len(active_types)}",
        )
    )

    status, dashboard = client.request("GET", "/api/v1/dashboard/current")
    # A brand-new installation legitimately has no Published snapshot, so 404 is operationally
    # acceptable. Any other error means the public read path is unhealthy.
    dashboard_ok = status in {200, 404}
    steps.append(Step("Dashboard read path", dashboard_ok, f"HTTP {status} {_detail(dashboard)}"))

    if status == 200:
        factor_status, factor_health = client.request("GET", "/api/v1/admin/factors/health")
        factor_ok = (
            factor_status == 200
            and int((factor_health or {}).get("summary", {}).get("logical_factors", 0)) == 27
        )
        steps.append(Step("Factor health", factor_ok, f"HTTP {factor_status} {_detail(factor_health)}"))
    else:
        steps.append(Step("Factor health", True, "Skipped: no Published snapshot yet", required=False))

    if trigger or existing_run_id:
        run_id = existing_run_id
        if run_id:
            steps.append(
                Step(
                    "Manual preview dispatch",
                    True,
                    f"RESUME existing analysis_run_id={run_id}; no new analysis dispatched",
                )
            )
        else:
            status, started = client.request(
                "POST",
                "/api/v1/admin/analysis/run",
                {
                    "run_mode": "FULL_REFRESH",
                    "publish_mode": "PREVIEW_ONLY",
                    "force_refresh": True,
                },
                timeout=15,
            )
            run_id = (started or {}).get("analysis_run_id") if isinstance(started, dict) else None
            start_ok = status == 202 and bool(run_id)
            steps.append(Step("Manual preview dispatch", start_ok, f"HTTP {status} {_detail(started)}"))
            if not start_ok:
                return _finish(steps)

        terminal = {
            "PREVIEW_READY", "QUALITY_FAILED", "PUBLISHED", "FAILED",
            "DATA_READY", "CANCELLED", "SKIPPED"
        }
        deadline = time.monotonic() + max(5, max_poll_seconds)
        result = None
        last_status = None
        last_progress_print = 0.0
        while time.monotonic() < deadline:
            poll_status, result = client.request("GET", f"/api/v1/admin/runs/{run_id}")
            if poll_status == 200 and isinstance(result, dict):
                current_status = result.get("status")
                if current_status in terminal:
                    break
                now = time.monotonic()
                if current_status != last_status or now - last_progress_print >= 15:
                    remaining = max(0, int(deadline - now))
                    print(
                        f"[WAIT] analysis_run_id={run_id} status={current_status} "
                        f"remaining={remaining}s",
                        flush=True,
                    )
                    last_status = current_status
                    last_progress_print = now
            time.sleep(2)

        run_status = (result or {}).get("status") if isinstance(result, dict) else None
        # A polling timeout means the test did not observe a terminal state within its budget;
        # it is not rewritten as an application FAILED state.  The same run can be resumed with
        # WIDEGOLD_SMOKE_RUN_ID / docker-e2e.ps1 -RunId without spending tokens on a duplicate run.
        preview_ok = run_status in {
            "PREVIEW_READY", "QUALITY_FAILED", "PUBLISHED", "DATA_READY", "SKIPPED"
        }
        detail = _detail(result)
        if run_status not in terminal:
            detail = (
                f"poll timeout after {max_poll_seconds}s; run remains {run_status}; "
                f"resume analysis_run_id={run_id}. last={detail}"
            )
        steps.append(Step("Manual preview terminal", preview_ok, detail))
        if run_status in {"PREVIEW_READY", "QUALITY_FAILED", "PUBLISHED"}:
            fh_status, fh = client.request("GET", f"/api/v1/admin/factors/health?run_id={run_id}")
            fh_ok = fh_status == 200 and int((fh or {}).get("summary", {}).get("logical_factors", 0)) == 27
            steps.append(Step("Preview factor health", fh_ok, f"HTTP {fh_status} {_detail(fh)}"))

    return _finish(steps)


def _finish(steps: list[Step]) -> int:
    print("WideGold Production Smoke")
    print("=========================")
    for step in steps:
        marker = "PASS" if step.ok else ("WARN" if not step.required else "FAIL")
        requirement = "required" if step.required else "optional"
        print(f"[{marker:4}] {step.name} ({requirement})")
        print(f"       {step.detail}")
    failed = [step for step in steps if step.required and not step.ok]
    print("-------------------------")
    print(f"required_failures={len(failed)} total_steps={len(steps)}")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
