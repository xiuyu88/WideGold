from __future__ import annotations

import os
from datetime import datetime, timezone

from widegold.external_bridge.adapters import (
    CONTRACT_VERSION,
    CentralBankGoldAdapter,
    DR007Adapter,
    EarningsRevisionAdapter,
    ForeignActivityAdapter,
    FreeSourceClient,
    GoldSupplyDemandAdapter,
)
from widegold.external_bridge.models import BridgeFetchRequest
from widegold.external_bridge.storage import BridgeStore


class ExternalBridgeService:
    def __init__(self) -> None:
        path = os.getenv("WIDEGOLD_EXTERNAL_BRIDGE_DB", "/data/bridge.sqlite3")
        self.store = BridgeStore(path)
        self.http = FreeSourceClient(self.store)
        adapters = [
            DR007Adapter(self.store, self.http),
            EarningsRevisionAdapter(self.store, self.http),
            ForeignActivityAdapter(self.store, self.http),
            CentralBankGoldAdapter(self.store, self.http),
            GoldSupplyDemandAdapter(self.store, self.http),
        ]
        self.adapters = {adapter.capability: adapter for adapter in adapters}

    @property
    def capabilities(self) -> list[str]:
        return sorted(self.adapters)

    def fetch(self, request: BridgeFetchRequest) -> dict:
        if request.contract_version != CONTRACT_VERSION:
            return {
                "contract_version": CONTRACT_VERSION,
                "capability": request.capability,
                "source_id": "FREE_EXTERNAL_BRIDGE",
                "status": "UNAVAILABLE",
                "observations": [],
                "warnings": [f"contract_version_mismatch:{request.contract_version}"],
            }
        adapter = self.adapters.get(request.capability)
        if adapter is None:
            return {
                "contract_version": CONTRACT_VERSION,
                "capability": request.capability,
                "source_id": "FREE_EXTERNAL_BRIDGE",
                "status": "UNAVAILABLE",
                "observations": [],
                "warnings": ["capability_not_supported"],
            }
        # Pre-seeded because an adapter that falls off the end of a branch returns None without
        # raising; the previous code then referenced an unbound name and turned a degraded source
        # into a 500 from the bridge.
        warning = "adapter_returned_no_result"
        try:
            result = adapter.fetch(request)
        except Exception as exc:
            result = None
            warning = f"adapter_unhandled_error:{type(exc).__name__}"
        if result is None:
            return {
                "contract_version": CONTRACT_VERSION,
                "capability": request.capability,
                "source_id": "FREE_EXTERNAL_BRIDGE",
                "status": "UNAVAILABLE",
                "observations": [],
                "warnings": [warning],
            }
        now = datetime.now(timezone.utc)
        observations = []
        warnings = list(result.warnings)
        for item in result.observations:
            release = item.get("release_ts")
            if release is not None:
                if release.tzinfo is None:
                    release = release.replace(tzinfo=timezone.utc)
                if release > request.as_of.astimezone(timezone.utc):
                    # One marker is enough; a wide look-ahead window would otherwise repeat this
                    # warning hundreds of times and drown the real ones.
                    if "bridge_dropped_release_after_as_of" not in warnings:
                        warnings.append("bridge_dropped_release_after_as_of")
                    continue
            normalized = dict(item)
            normalized.setdefault("ingest_ts", now)
            observations.append(normalized)
        status = result.status
        if status == "VALID" and not observations:
            status = "UNAVAILABLE"
        return {
            "contract_version": CONTRACT_VERSION,
            "capability": request.capability,
            "source_id": result.source_id,
            "status": status,
            "observations": observations,
            "warnings": warnings,
        }
