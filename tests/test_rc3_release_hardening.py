from __future__ import annotations

import importlib.util
import json
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_json_compatible_handles_yaml_dates() -> None:
    seed = _load_script("widegold_seed_reference_rc3", "scripts/seed_reference.py")
    raw = {
        "date": date(2026, 9, 11),
        "datetime": datetime(2026, 9, 11, 3, 30, tzinfo=timezone.utc),
        "nested": [date(2026, 1, 1)],
    }
    normalized = seed._json_compatible(raw)
    assert normalized["date"] == "2026-09-11"
    assert normalized["datetime"].startswith("2026-09-11T03:30:00")
    assert normalized["nested"] == ["2026-01-01"]
    json.dumps(normalized)


def test_bootstrap_runtime_bundle_is_json_serializable_and_complete() -> None:
    seed = _load_script("widegold_seed_reference_bundle_rc3", "scripts/seed_reference.py")
    bundle = seed.bootstrap_config_bundle()
    assert len(bundle) == 13
    assert "ASSET_REGISTRY" in bundle
    assert "CALENDAR" in bundle
    assert "SCORING_ENGINE" in bundle
    json.dumps(seed._json_compatible(bundle))


def test_vite_client_type_declaration_is_present() -> None:
    content = (ROOT / "apps/web/src/vite-env.d.ts").read_text(encoding="utf-8")
    assert 'reference types="vite/client"' in content


def test_nginx_uses_docker_dns_for_recreated_upstreams() -> None:
    content = (ROOT / "infra/nginx/default.conf").read_text(encoding="utf-8")
    assert "resolver 127.0.0.11" in content
    assert "proxy_pass http://$api_upstream" in content
    assert "proxy_pass http://$web_upstream" in content
