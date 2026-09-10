from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from widegold.external_bridge.adapters import DR007Adapter
from widegold.external_bridge.models import BridgeFetchRequest
from widegold.external_bridge.storage import BridgeStore


class FakeHttp:
    def get(self, url, **kwargs):
        assert url.endswith("/fdr-chrt.csv")
        return SimpleNamespace(
            content=(
                b"2026-09-08,1.30,1.38,1.40\n"
                b"2026-09-09,1.30,1.39,1.40\n"
                b"2026-09-10,1.40,1.41,1.40\n"
            )
        )


def test_dr007_uses_direct_chinamoney_fdr007_fixing_proxy(tmp_path):
    store = BridgeStore(str(tmp_path / "bridge.sqlite3"))
    adapter = DR007Adapter(store, FakeHttp())
    request = BridgeFetchRequest(
        contract_version="widegold.external-indicator.v1",
        capability="china_money.dr007",
        indicator_id="CN_DR007",
        asset_id=None,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 11),
        as_of=datetime(2026, 9, 11, 4, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        force_refresh=False,
        source_hint="CHINAMONEY_AKSHARE",
        params={},
    )

    result = adapter.fetch(request)
    assert result.status == "VALID"
    assert len(result.observations) == 3
    latest = result.observations[-1]
    assert latest["value"] == 1.41
    assert latest["metadata"]["benchmark"] == "DR007"
    assert latest["metadata"]["upstream_benchmark"] == "FDR007"
    assert latest["metadata"]["proxy"] is True
    assert latest["definition_version"] == "DR007_FDR007_FIXING_PROXY_FREE_V3"
    assert "dr007_free_source_uses_fdr007_fixing_proxy" in result.warnings
