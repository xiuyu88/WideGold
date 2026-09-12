from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Callable

import yaml

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT, ROOT / "src"):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)
ARTIFACTS = ROOT / "artifacts"
SECRET_RE = re.compile(r"sk-[A-Za-z0-9_-]{12,}")
TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".toml", ".md", ".json", ".sh", ".ps1", ".tsx", ".ts", ".js"}

TEST_ENV_OVERRIDES = {
    "WIDEGOLD_ENV": "test",
    "WIDEGOLD_PERSISTENCE": "memory",
    "WIDEGOLD_RUNTIME_CONFIG_SOURCE": "yaml",
    "WIDEGOLD_ORCHESTRATION_MODE": "direct",
    "WIDEGOLD_DATA_MODE": "mock",
    "WIDEGOLD_EVENT_GRAPH_MODE": "mock",
    "WIDEGOLD_EXPLANATION_MODE": "deterministic",
    "WIDEGOLD_NEWS_MODE": "disabled",
    "WIDEGOLD_RESEARCH_MODE": "mock",
    "WIDEGOLD_AUTH_MODE": "disabled",
    "WIDEGOLD_RUN_LOCK_ENABLED": "false",
    "WIDEGOLD_DASHBOARD_CACHE_ENABLED": "false",
    "WIDEGOLD_LANGGRAPH_CHECKPOINT_MODE": "memory",
    # Empty values take precedence over .env in the subprocess and prevent release tests from
    # reaching production data/LLM providers accidentally.
    "WIDEGOLD_EXTERNAL_INDICATOR_URL": "",
    "WIDEGOLD_FACTOR_FEED_URL": "",
    "FRED_API_KEY": "",
    "QWEN_API_KEY": "",
    "DEEPSEEK_API_KEY": "",
    "GPT_COMPAT_API_KEY": "",
    "SEARCH_PROVIDER": "",
    "SEARCH_API_KEY": "",
    "SEARCH_BASE_URL": "",
}


def _run(
    name: str,
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> dict:
    completed = subprocess.run(
        cmd,
        cwd=cwd or ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "name": name,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def _perform(name: str, check: Callable[[], dict]) -> dict:
    print(f"[RUN ] {name} ...", flush=True)
    started = monotonic()
    try:
        result = check()
    except Exception as exc:  # Gate must report the failing stage instead of disappearing.
        result = {
            "name": name,
            "status": "FAIL",
            "details": {"exception": f"{type(exc).__name__}: {exc}"},
        }
    duration = round(monotonic() - started, 3)
    result.setdefault("name", name)
    result["duration_seconds"] = duration
    print(f"[{result['status']:<4}] {name} ({duration:.1f}s)", flush=True)
    if result["status"] == "FAIL":
        tail = result.get("stderr_tail") or result.get("stdout_tail")
        if tail:
            print(tail[-2000:], file=sys.stderr, flush=True)
    return result


def _config_check() -> dict:
    from widegold.services.config_validation import validate_runtime_config

    validate_runtime_config.cache_clear()
    result = validate_runtime_config()
    errors = [item.model_dump(mode="json") for item in result.issues if item.level == "ERROR"]
    return {
        "name": "runtime_config",
        "status": "PASS" if result.valid and not errors else "FAIL",
        "details": {
            "factor_count": result.factor_count,
            "calculator_count": result.calculator_count,
            "required_indicator_count": result.required_indicator_count,
            "external_indicator_count": result.external_indicator_count,
            "errors": errors,
        },
    }


def _yaml_check() -> dict:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    prefect = yaml.safe_load((ROOT / "prefect.yaml").read_text(encoding="utf-8"))
    services = (compose or {}).get("services") or {}
    ok = isinstance(services, dict) and len(services) >= 1 and isinstance(prefect, dict)
    return {
        "name": "yaml_static",
        "status": "PASS" if ok else "FAIL",
        "details": {"compose_services": len(services), "prefect_loaded": isinstance(prefect, dict)},
    }



def _final_env_check() -> dict:
    path = ROOT / ".env.final.example"
    required = {
        "WIDEGOLD_DATA_MODE=raw_indicators",
        "WIDEGOLD_EXTERNAL_INDICATOR_URL=http://external-bridge:9100",
        "WIDEGOLD_EVENT_GRAPH_MODE=llm",
        "DEEPSEEK_BASE_URL=https://api.deepseek.com",
        "WIDEGOLD_SMOKE_MAX_POLL_SECONDS=600",
    }
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    missing = sorted(item for item in required if item not in text)
    return {
        "name": "final_env_template",
        "status": "PASS" if path.exists() and not missing else "FAIL",
        "details": {"exists": path.exists(), "missing": missing},
    }


def _external_bridge_static_check() -> dict:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8")) or {}
    services = compose.get("services") or {}
    service = services.get("external-bridge") or {}
    files = [
        ROOT / "apps" / "external_bridge" / "main.py",
        ROOT / "src" / "widegold" / "external_bridge" / "service.py",
        ROOT / "src" / "widegold" / "external_bridge" / "adapters.py",
        ROOT / "scripts" / "external_bridge_smoke.py",
    ]
    missing_files = [str(x.relative_to(ROOT)) for x in files if not x.exists()]
    env = service.get("environment") or {}
    ok = bool(service) and not missing_files and "WIDEGOLD_EXTERNAL_INDICATOR_API_KEY" in env
    return {
        "name": "external_bridge_static",
        "status": "PASS" if ok else "FAIL",
        "details": {
            "service_present": bool(service),
            "missing_files": missing_files,
            "healthcheck_present": bool(service.get("healthcheck")),
        },
    }

def _machine_output_check(env: dict[str, str]) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "widegold.mock_runner"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    details: dict = {"returncode": completed.returncode, "stderr_tail": completed.stderr[-2000:]}
    ok = completed.returncode == 0
    if ok:
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            ok = False
            details["json_error"] = str(exc)
        else:
            details.update(
                {
                    "contract_version": payload.get("contract_version"),
                    "assets": len(payload.get("assets") or []),
                    "factor_states": len(payload.get("factor_states") or []),
                    "logical_factors": (payload.get("factor_resolution") or {}).get("total_factors"),
                    "status": payload.get("status"),
                }
            )
            ok = ok and payload.get("contract_version") == "widegold.analysis.v1"
            ok = ok and len(payload.get("assets") or []) == 7
            ok = ok and len(payload.get("factor_states") or []) == 42
            ok = ok and (payload.get("factor_resolution") or {}).get("total_factors") == 27
            ok = ok and completed.stdout.count("\n") == 1
    return {"name": "machine_output_contract", "status": "PASS" if ok else "FAIL", "details": details}


def _frontend_build_check() -> dict:
    """Validate the production frontend using the same Docker build path as deployment.

    A release host invoked with --require-docker should not need a separate host Node/npm setup.
    For source-only review without Docker, an already-installed local node_modules tree can still
    be used; otherwise this check is explicitly SKIP rather than silently downloading packages.
    """
    docker = shutil.which("docker")
    if docker:
        result = _run("frontend_build", [docker, "compose", "build", "web"])
        result["details"] = {"backend": "docker-compose-build-web"}
        return result

    npm = shutil.which("npm")
    web_dir = ROOT / "apps" / "web"
    if npm and (web_dir / "node_modules").exists():
        result = _run("frontend_build", [npm, "run", "build"], cwd=web_dir)
        result["details"] = {"backend": "host-npm-existing-node_modules"}
        return result

    return {
        "name": "frontend_build",
        "status": "SKIP",
        "details": {
            "reason": "docker unavailable and host node_modules is not installed",
            "hint": "run on the Docker release host or install frontend dependencies first",
        },
    }


def _secret_scan() -> dict:
    matches: list[str] = []
    # Generated reports may contain captured command output and must not recursively scan
    # themselves. Source and configuration files remain in scope.
    excluded = {".git", ".pytest_cache", "artifacts", "node_modules", "__pycache__", "dist"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in excluded for part in path.parts):
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if SECRET_RE.search(text):
            matches.append(str(path.relative_to(ROOT)))
    return {
        "name": "secret_scan",
        "status": "PASS" if not matches else "FAIL",
        "details": {"matches": matches[:50]},
    }


def _docker_check(require_docker: bool) -> dict:
    docker = shutil.which("docker")
    if not docker:
        return {
            "name": "docker_compose_config",
            "status": "FAIL" if require_docker else "SKIP",
            "details": {"reason": "docker CLI not available in this environment"},
        }
    # Validation only: rendered Compose output expands .env secrets and must never be persisted
    # in the release report.
    return _run("docker_compose_config", [docker, "compose", "config", "--quiet"])


def main() -> int:
    parser = argparse.ArgumentParser(description="WideGold V1 Final release gate")
    parser.add_argument(
        "--require-docker",
        action="store_true",
        help="fail instead of skip when Docker CLI/daemon validation is unavailable",
    )
    parser.add_argument("--skip-tests", action="store_true", help="skip pytest for a faster local precheck")
    args = parser.parse_args()

    ARTIFACTS.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "src"), env.get("PYTHONPATH", "")])
    test_env = {**env, **TEST_ENV_OVERRIDES}

    checks: list[dict] = []
    if not args.skip_tests:
        checks.append(
            _perform(
                "pytest",
                lambda: _run("pytest", [sys.executable, "-m", "pytest", "-q"], env=test_env),
            )
        )
    checks.append(
        _perform(
            "compileall",
            lambda: _run(
                "compileall",
                [sys.executable, "-m", "compileall", "-q", "apps", "src", "scripts"],
                env=env,
            ),
        )
    )
    checks.append(_perform("runtime_config", _config_check))
    checks.append(_perform("yaml_static", _yaml_check))
    checks.append(_perform("final_env_template", _final_env_check))
    checks.append(_perform("external_bridge_static", _external_bridge_static_check))
    checks.append(_perform("machine_output_contract", lambda: _machine_output_check(test_env)))
    checks.append(_perform("frontend_build", _frontend_build_check))
    checks.append(
        _perform(
            "alembic_offline",
            lambda: _run(
                "alembic_offline",
                [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
                env=env,
            ),
        )
    )
    checks.append(_perform("secret_scan", _secret_scan))
    checks.append(_perform("docker_compose_config", lambda: _docker_check(args.require_docker)))

    failures = [item for item in checks if item["status"] == "FAIL"]
    skips = [item for item in checks if item["status"] == "SKIP"]
    report = {
        "gate_version": "widegold.release-gate.v3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_ready": not failures and (not args.require_docker or not skips),
        "require_docker": args.require_docker,
        "checks": checks,
    }
    report_path = ARTIFACTS / "release_gate_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-------------------------", flush=True)
    for item in checks:
        print(f"[{item['status']}] {item['name']} ({item.get('duration_seconds', 0):.1f}s)", flush=True)
    print(f"report={report_path.relative_to(ROOT)}", flush=True)
    if failures:
        print("Release gate failed: " + ", ".join(item["name"] for item in failures), file=sys.stderr)
        return 1
    if skips:
        print(
            "Code/static gate passed with skipped environment checks: "
            + ", ".join(item["name"] for item in skips)
        )
    else:
        print("Release gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
