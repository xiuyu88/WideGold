from __future__ import annotations

import calendar
import html
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx

from widegold.domain.enums import DataStatus
from widegold.schemas.indicators import IndicatorFetchRequest, IndicatorObservation, IndicatorSeries

_SHANGHAI = ZoneInfo("Asia/Shanghai")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)

    def text(self) -> str:
        return " ".join(self.parts)


def _html_text(raw: str) -> str:
    parser = _TextExtractor()
    parser.feed(html.unescape(raw))
    return re.sub(r"\s+", " ", parser.text()).strip()


def _release_timestamp(text: str) -> datetime | None:
    patterns = (
        r"(?:发布时间[:：]?\s*)?(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\s+(\d{1,2}):(\d{2})",
        r"(?:发布时间[:：]?\s*)?(20\d{2})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            year, month, day, hour, minute = map(int, match.groups())
            return datetime(year, month, day, hour, minute, tzinfo=_SHANGHAI)
    return None


def _industrial_profit_ytd(text: str) -> tuple[date, float, dict[str, Any]] | None:
    year_match = re.search(r"(20\d{2})年", text)
    if not year_match:
        return None
    year = int(year_match.group(1))

    period = re.search(r"1\s*[—–－-]\s*(\d{1,2})月份", text)
    if period:
        end_month = int(period.group(1))
    elif re.search(rf"{year}年[^。]{{0,80}}全国规模以上工业企业", text):
        end_month = 12
    else:
        return None
    if not 1 <= end_month <= 12:
        return None

    sentence = re.search(
        r"全国规模以上工业企业[^。；]{0,120}?利润总额[^。；]{0,120}?"
        r"(同比增长|同比下降|比上年增长|比上年下降)\s*([0-9]+(?:\.[0-9]+)?)%",
        text,
    )
    if not sentence:
        return None
    direction, raw_value = sentence.groups()
    value = float(raw_value)
    if "下降" in direction:
        value = -value

    day = calendar.monthrange(year, end_month)[1]
    return (
        date(year, end_month, day),
        value,
        {
            "aggregation_scope": "ytd",
            "period_start_month": 1,
            "period_end_month": end_month,
        },
    )


def _article_links(raw_html: str, base_url: str) -> list[str]:
    result: list[str] = []
    for match in re.finditer(
        r"<a\b[^>]*?href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<body>.*?)</a>",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        label = _html_text(match.group("body"))
        if "规模以上工业企业" not in label or "利润" not in label:
            continue
        url = urljoin(base_url, html.unescape(match.group("href")))
        if url not in result:
            result.append(url)
    return result


class NbsIndustrialProfitProvider:
    """Official NBS industrial-profit YTD adapter with point-in-time release protection.

    The provider intentionally extracts the cumulative/YTD total-profit YoY value published by
    China's National Bureau of Statistics. It does not mix the article's separate single-month
    growth figure into the same series.
    """

    provider_name = "nbs"

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._client = client

    def _get(self, client: httpx.Client, url: str) -> str:
        response = client.get(url, headers={"User-Agent": "WideGold/1.0 NBS data adapter"})
        response.raise_for_status()
        return response.text

    def fetch(self, request: IndicatorFetchRequest, spec: dict) -> IndicatorSeries:
        source_id = str(spec.get("source_id") or "NBS")
        if request.indicator_id != "CN_INDUSTRIAL_PROFIT_YOY":
            return IndicatorSeries(
                indicator_id=request.indicator_id,
                source_id=source_id,
                status=DataStatus.UNAVAILABLE,
                warnings=[f"nbs_provider_unsupported_indicator:{request.indicator_id}"],
            )

        params = spec.get("params") or {}
        listing_url = str(params.get("listing_url") or "https://www.stats.gov.cn/sj/zxfb/")
        max_pages = max(1, min(int(params.get("max_pages", 6)), 20))
        explicit_urls = [str(item) for item in params.get("article_urls", [])]
        warnings: list[str] = []
        client = self._client or httpx.Client(timeout=self.timeout_seconds, follow_redirects=True)
        close_client = self._client is None

        try:
            article_urls = list(dict.fromkeys(explicit_urls))
            if not article_urls:
                for page in range(max_pages):
                    page_url = listing_url if page == 0 else urljoin(listing_url, f"index_{page}.html")
                    try:
                        page_html = self._get(client, page_url)
                    except Exception as exc:
                        warnings.append(f"nbs_listing_fetch_failed:{page_url}:{exc.__class__.__name__}")
                        if page == 0:
                            break
                        continue
                    for url in _article_links(page_html, page_url):
                        if url not in article_urls:
                            article_urls.append(url)

            observations: list[IndicatorObservation] = []
            for url in article_urls:
                try:
                    raw_html = self._get(client, url)
                except Exception as exc:
                    warnings.append(f"nbs_article_fetch_failed:{url}:{exc.__class__.__name__}")
                    continue
                text = _html_text(raw_html)
                release_ts = _release_timestamp(text)
                parsed = _industrial_profit_ytd(text)
                if release_ts is None or parsed is None:
                    warnings.append(f"nbs_article_parse_failed:{url}")
                    continue
                observation_date, value, metadata = parsed
                # Point-in-time protection: an article published after as_of cannot enter replay.
                if release_ts > request.as_of:
                    continue
                if not request.start_date <= observation_date <= request.end_date:
                    continue
                observations.append(
                    IndicatorObservation(
                        indicator_id=request.indicator_id,
                        observation_date=observation_date,
                        release_ts=release_ts,
                        ingest_ts=datetime.now(timezone.utc),
                        value=value,
                        source_id=source_id,
                        revision_vintage=release_ts.date().isoformat(),
                        definition_version=str(
                            spec.get("definition_version") or "NBS_INDUSTRIAL_PROFIT_YTD_V1"
                        ),
                        status=DataStatus.VALID,
                        metadata={**metadata, "source_url": url, "source_authority": "NBS"},
                    )
                )
        finally:
            if close_client:
                client.close()

        by_date: dict[date, IndicatorObservation] = {}
        for row in sorted(observations, key=lambda item: (item.observation_date, item.release_ts)):
            by_date[row.observation_date] = row
        rows = [by_date[key] for key in sorted(by_date)]
        return IndicatorSeries(
            indicator_id=request.indicator_id,
            source_id=source_id,
            observations=rows,
            status=DataStatus.VALID if rows else DataStatus.UNAVAILABLE,
            warnings=warnings if rows else [*warnings, "nbs_industrial_profit_no_usable_observations"],
        )
