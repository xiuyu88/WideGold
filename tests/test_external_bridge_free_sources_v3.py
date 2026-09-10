from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import httpx

from widegold.external_bridge.adapters import (
    BRIDGE_BUILD,
    CentralBankGoldAdapter,
    EarningsRevisionAdapter,
    ForeignActivityAdapter,
    GoldSupplyDemandAdapter,
    _wgc_quarter_table,
)
from widegold.external_bridge.models import BridgeFetchRequest
from widegold.external_bridge.storage import BridgeStore


def request(capability: str, indicator: str, asset: str | None = None) -> BridgeFetchRequest:
    return BridgeFetchRequest(
        contract_version="widegold.external-indicator.v1",
        capability=capability,
        indicator_id=indicator,
        asset_id=asset,
        start_date=date(2025, 1, 1),
        end_date=date(2026, 9, 11),
        as_of=datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc),
        params={},
    )


WGC_HTML = """
<html><body><p>30 July, 2026</p><p>Data as of 30 June 2026.</p>
<table>
<tr><th></th><th>Q2'25</th><th>Q3'25</th><th>Q4'25</th><th>Q1'26</th><th>Q2'26</th><th>q/q % change</th><th>y/y % change</th></tr>
<tr><td>Mine Production</td><td>947.7</td><td>1028.1</td><td>986.6</td><td>901.3</td><td>965.6</td><td>7</td><td>2</td></tr>
<tr><td>Net Producer Hedging</td><td>-25.8</td><td>-0.2</td><td>-20.2</td><td>-22.1</td><td>-22.8</td><td>-</td><td>-</td></tr>
<tr><td>Recycled Gold</td><td>346.7</td><td>342.7</td><td>365.8</td><td>373.8</td><td>326.1</td><td>-13</td><td>-6</td></tr>
<tr><td>Jewellery Fabrication</td><td>354.2</td><td>420.0</td><td>438.9</td><td>332.1</td><td>310.3</td><td>-7</td><td>-12</td></tr>
<tr><td>Technology</td><td>78.6</td><td>81.7</td><td>82.1</td><td>81.6</td><td>80.4</td><td>-2</td><td>2</td></tr>
<tr><td>Investment</td><td>486.8</td><td>554.1</td><td>603.9</td><td>539.2</td><td>262.2</td><td>-51</td><td>-46</td></tr>
<tr><td>Central Banks &amp; Other inst.</td><td>177.9</td><td>226.3</td><td>208.2</td><td>56.5</td><td>288.9</td><td>411</td><td>62</td></tr>
</table></body></html>
"""


class WgcHttp:
    def get(self, url, **kwargs):
        return SimpleNamespace(text=WGC_HTML, content=WGC_HTML.encode())


def test_wgc_html_parser_selects_latest_quarter_not_percent_columns():
    obs_date, frame = _wgc_quarter_table(WGC_HTML)
    assert obs_date == date(2026, 6, 30)
    rows = dict(zip(frame[0], frame[1]))
    assert rows["Central Banks & Other inst."] == 288.9
    assert rows["Mine Production"] == 965.6


def test_wgc_adapters_use_public_html_fallback(tmp_path):
    store = BridgeStore(str(tmp_path / "bridge.sqlite3"))
    central = CentralBankGoldAdapter(store, WgcHttp())
    supply = GoldSupplyDemandAdapter(store, WgcHttp())
    c = central.fetch(request("gold.global_central_bank_demand", "GLOBAL_CENTRAL_BANK_GOLD"))
    g = supply.fetch(request("gold.supply_fabrication_demand", "GOLD_SUPPLY_DEMAND"))
    assert c.status == "VALID"
    assert c.observations[0]["value"] == 288.9
    assert c.observations[0]["observation_date"] == date(2026, 6, 30)
    assert g.status == "VALID"
    assert -100 <= g.observations[0]["value"] <= 100
    assert "source_url" in g.observations[0]["metadata"]


class BrokenForeignHttp:
    def get(self, url, **kwargs):
        raise httpx.ReadTimeout("simulated")


def test_foreign_activity_public_source_failure_degrades_fast(tmp_path):
    store = BridgeStore(str(tmp_path / "bridge.sqlite3"))
    adapter = ForeignActivityAdapter(store, BrokenForeignHttp())
    result = adapter.fetch(request("china_equity.foreign_activity", "CN_FOREIGN_ACTIVITY"))
    assert result.status == "UNAVAILABLE"
    assert result.warnings == ["foreign_activity_source_unavailable:ReadTimeout"]


class ForecastHttp:
    def __init__(self):
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        data = {
            "result": {
                "pages": 1,
                "data": [
                    {
                        "SECURITY_CODE": "600000",
                        "YEAR1": 2026,
                        "EPS1": 1.20,
                        "YEAR2": 2027,
                        "EPS2": 1.30,
                        "YEAR3": 2028,
                        "EPS3": 1.40,
                        "YEAR4": 2029,
                        "EPS4": 1.50,
                    }
                ],
            }
        }
        return SimpleNamespace(content=json.dumps(data).encode())


def test_earnings_forecast_direct_http_is_process_cached(tmp_path):
    store = BridgeStore(str(tmp_path / "bridge.sqlite3"))
    http = ForecastHttp()
    adapter = EarningsRevisionAdapter(store, http)
    first = adapter._forecast_frame()
    second = adapter._forecast_frame()
    assert http.calls == 1
    assert first.loc[0, "代码"] == "600000"
    assert first.loc[0, "2026预测每股收益"] == 1.2
    assert second.equals(first)


def test_bridge_build_marker_is_v3():
    assert BRIDGE_BUILD == "2026.09.11-free-sources-v3.1"


class ForecastSixPageHttp:
    def __init__(self):
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        import re as _re
        match = _re.search(r"(?:pageNumber|pageNo|pageNum)=(\d+)", url)
        page = int(match.group(1)) if match else 1
        data = {
            "result": {
                "pages": 6,
                "data": [
                    {
                        "SECURITY_CODE": f"{600000 + page:06d}",
                        "YEAR1": 2026,
                        "EPS1": 1.0 + page / 100.0,
                    }
                ],
            }
        }
        return SimpleNamespace(content=json.dumps(data).encode())


def test_earnings_forecast_accepts_current_six_page_eastmoney_response(tmp_path):
    store = BridgeStore(str(tmp_path / "bridge.sqlite3"))
    http = ForecastSixPageHttp()
    adapter = EarningsRevisionAdapter(store, http)
    frame = adapter._load_forecast_frame_uncached()
    assert http.calls == 6
    assert len(frame) == 6
    assert frame["代码"].tolist()[0] == "600001"
