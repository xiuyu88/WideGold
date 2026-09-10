from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from widegold.data.providers.nbs_industrial_profit import NbsIndustrialProfitProvider
from widegold.domain.enums import DataStatus
from widegold.schemas.indicators import IndicatorFetchRequest

SH = ZoneInfo("Asia/Shanghai")
LISTING = "https://www.stats.gov.cn/sj/zxfb/"
ARTICLE = "https://www.stats.gov.cn/sj/zxfb/202608/t20260827_1965126.html"


def _provider(as_of: datetime):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == LISTING:
            return httpx.Response(
                200,
                text=f'<html><a href="/sj/zxfb/202608/t20260827_1965126.html">2026年1—7月份全国规模以上工业企业利润增长17.6%</a></html>',
            )
        if url == ARTICLE:
            return httpx.Response(
                200,
                text="""
                <html><body>
                <div>2026/08/27 09:30</div>
                <h1>2026年1—7月份全国规模以上工业企业利润增长17.6%</h1>
                <p>1—7月份，全国规模以上工业企业实现利润总额45820.6亿元，同比增长17.6%。</p>
                <p>7月份，规模以上工业企业利润同比增长15.1%。</p>
                </body></html>
                """,
            )
        # page 2+ discovery is optional and may be missing in this compact fixture.
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    request = IndicatorFetchRequest(
        indicator_id="CN_INDUSTRIAL_PROFIT_YOY",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 9, 10),
        as_of=as_of,
    )
    spec = {
        "source_id": "NBS",
        "definition_version": "NBS_INDUSTRIAL_PROFIT_YTD_V1",
        "params": {"listing_url": LISTING, "max_pages": 1},
    }
    return NbsIndustrialProfitProvider(client=client).fetch(request, spec)


def test_nbs_provider_extracts_ytd_profit_and_real_release_time():
    result = _provider(datetime(2026, 9, 10, 18, 10, tzinfo=SH))

    assert result.status == DataStatus.VALID
    assert len(result.observations) == 1
    row = result.observations[0]
    assert row.observation_date == date(2026, 7, 31)
    assert row.value == 17.6
    assert row.release_ts == datetime(2026, 8, 27, 9, 30, tzinfo=SH)
    assert row.metadata["aggregation_scope"] == "ytd"
    assert row.metadata["period_end_month"] == 7
    assert row.metadata["source_url"] == ARTICLE


def test_nbs_provider_replay_does_not_leak_future_release():
    result = _provider(datetime(2026, 8, 26, 18, 10, tzinfo=SH))
    assert result.status == DataStatus.UNAVAILABLE
    assert result.observations == []
