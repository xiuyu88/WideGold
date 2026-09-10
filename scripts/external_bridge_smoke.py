from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CAPABILITIES = [
    ("china_money.dr007", "CN_DR007", None),
    *[("equity_index.earnings_revision", "CN_INDEX_EARNINGS_REV", asset) for asset in
      ("CSI300", "CSI_A500", "CSI500", "CSI1000", "CHINEXT", "STAR50")],
    ("china_equity.foreign_activity", "CN_FOREIGN_ACTIVITY", None),
    ("gold.global_central_bank_demand", "GLOBAL_CENTRAL_BANK_GOLD", None),
    ("gold.supply_fabrication_demand", "GOLD_SUPPLY_DEMAND", None),
]


def dotenv(name: str) -> str | None:
    p = Path(".env")
    if not p.exists():
        return None
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        if raw.startswith(name + "="):
            return raw.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-data", action="store_true")
    args = parser.parse_args()
    base = os.getenv("WIDEGOLD_EXTERNAL_BRIDGE_HOST_URL") or f"http://localhost:{dotenv('EXTERNAL_BRIDGE_PORT') or '9100'}"
    key = os.getenv("WIDEGOLD_EXTERNAL_INDICATOR_API_KEY") or dotenv("WIDEGOLD_EXTERNAL_INDICATOR_API_KEY") or ""
    now = datetime.now(timezone.utc)
    failures = 0
    print("WideGold External Bridge Smoke")
    print("==============================")
    try:
        health_req = urllib.request.Request(base.rstrip("/") + "/health")
        with urllib.request.urlopen(health_req, timeout=10) as response:
            health = json.load(response)
        print(f"Bridge build: {health.get('build', 'legacy/unknown')}")
    except Exception as exc:
        print(f"[FAIL] bridge health: {type(exc).__name__}: {exc}")
        return 2
    for capability, indicator_id, asset_id in CAPABILITIES:
        body = {
            "contract_version": "widegold.external-indicator.v1",
            "capability": capability,
            "indicator_id": indicator_id,
            "asset_id": asset_id,
            "start_date": (date.today() - timedelta(days=800)).isoformat(),
            "end_date": date.today().isoformat(),
            "as_of": now.isoformat(),
            "force_refresh": False,
            "source_hint": None,
            "params": {},
        }
        req = urllib.request.Request(
            base.rstrip("/") + "/fetch",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        label = f"{indicator_id}{'::' + asset_id if asset_id else ''}"
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                payload = json.load(response)
        except Exception as exc:
            failures += 1
            print(f"[FAIL] {label}: {type(exc).__name__}: {exc}")
            continue
        status = payload.get("status")
        count = len(payload.get("observations") or [])
        warnings = payload.get("warnings") or []
        if status == "VALID" and count:
            print(f"[PASS] {label}: VALID observations={count}")
        elif (
            not args.require_data
            and indicator_id == "CN_INDEX_EARNINGS_REV"
            and status == "UNAVAILABLE"
            and any(str(w).startswith("earnings_revision_history_warming:") for w in warnings)
        ):
            print(f"[WARM] {label}: {warnings}")
        elif not args.require_data and status in {"UNAVAILABLE", "PARTIAL"}:
            print(f"[WARN] {label}: status={status} warnings={warnings}")
        else:
            failures += 1
            print(f"[FAIL] {label}: status={status} warnings={warnings}")
    print("------------------------------")
    print(f"failures={failures} scopes={len(CAPABILITIES)} require_data={args.require_data}")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
