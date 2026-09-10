from __future__ import annotations

import io
import json
import math
import os
import re
import threading
import time
from html.parser import HTMLParser
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urljoin
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd

from widegold.external_bridge.models import BridgeFetchRequest
from widegold.external_bridge.storage import BridgeStore

CONTRACT_VERSION = "widegold.external-indicator.v1"
BRIDGE_BUILD = "2026.09.11-free-sources-v3.1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc


@dataclass
class AdapterResult:
    source_id: str
    status: str
    observations: list[dict[str, Any]]
    warnings: list[str]


def _unavailable(source_id: str, warning: str) -> AdapterResult:
    return AdapterResult(source_id, "UNAVAILABLE", [], [warning])


def _as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _first_existing(columns: list[str], candidates: list[str]) -> str | None:
    by_normalized = {str(c).strip(): str(c) for c in columns}
    for candidate in candidates:
        if candidate in by_normalized:
            return by_normalized[candidate]
    return None


def _release_ts(observation_date: date, hour: int = 23, minute: int = 59) -> datetime:
    # Conservative timestamp: never make a daily/quarterly public observation available before
    # the end of the publication day when the source does not expose an exact timestamp.
    return datetime.combine(observation_date, dt_time(hour, minute), tzinfo=SHANGHAI)


class FreeSourceClient:
    def __init__(self, store: BridgeStore) -> None:
        self.store = store
        self.attempts = max(1, int(os.getenv("WIDEGOLD_EXTERNAL_BRIDGE_RETRY_ATTEMPTS", "2")))
        self.delay = max(0.0, float(os.getenv("WIDEGOLD_EXTERNAL_BRIDGE_RETRY_DELAY_SECONDS", "0.4")))
        self.timeout = max(3.0, float(os.getenv("WIDEGOLD_EXTERNAL_BRIDGE_HTTP_TIMEOUT_SECONDS", "20")))
        self.user_agent = os.getenv(
            "WIDEGOLD_EXTERNAL_BRIDGE_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124.0 WideGold-Free-Bridge/1.0",
        )
        self._failure_lock = threading.Lock()
        self._failure_until: dict[str, tuple[datetime, str]] = {}

    def get(
        self,
        url: str,
        *,
        cache_seconds: int = 0,
        headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        attempts: int | None = None,
    ) -> httpx.Response:
        now = datetime.now(UTC)
        key = f"GET:{url}"
        with self._failure_lock:
            failed = self._failure_until.get(key)
            if failed is not None:
                until, message = failed
                if now < until:
                    raise RuntimeError(f"source_backoff:{message}")
                self._failure_until.pop(key, None)
        if cache_seconds > 0:
            cached = self.store.cache_get(key, now)
            if cached is not None:
                content, content_type = cached
                return httpx.Response(
                    200,
                    content=content,
                    headers={"content-type": content_type or "application/octet-stream"},
                    request=httpx.Request("GET", url),
                )

        merged_headers = {"User-Agent": self.user_agent}
        if headers:
            merged_headers.update(headers)
        request_timeout = self.timeout if timeout_seconds is None else max(2.0, timeout_seconds)
        request_attempts = self.attempts if attempts is None else max(1, attempts)
        last: Exception | None = None
        for attempt in range(request_attempts):
            try:
                with httpx.Client(
                    timeout=request_timeout,
                    follow_redirects=True,
                    headers=merged_headers,
                ) as client:
                    response = client.get(url)
                    response.raise_for_status()
                if cache_seconds > 0:
                    self.store.cache_put(
                        key,
                        response.content,
                        response.headers.get("content-type"),
                        now,
                        now + timedelta(seconds=cache_seconds),
                    )
                with self._failure_lock:
                    self._failure_until.pop(key, None)
                return response
            except Exception as exc:  # public endpoints are allowed to degrade
                last = exc
                if attempt + 1 < request_attempts:
                    time.sleep(self.delay * (attempt + 1))
        assert last is not None
        with self._failure_lock:
            self._failure_until[key] = (
                datetime.now(UTC) + timedelta(seconds=60),
                type(last).__name__,
            )
        raise last


class BaseAdapter:
    capability: str

    def __init__(self, store: BridgeStore, http: FreeSourceClient) -> None:
        self.store = store
        self.http = http

    def fetch(self, request: BridgeFetchRequest) -> AdapterResult:
        raise NotImplementedError


class DR007Adapter(BaseAdapter):
    capability = "china_money.dr007"
    FDR_CSV_URL = (
        "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/fdr-chrt.csv"
    )

    def _query_fdr007(self) -> pd.DataFrame:
        response = self.http.get(
            self.FDR_CSV_URL,
            cache_seconds=15 * 60,
            headers={"Referer": "https://www.chinamoney.com.cn/chinese/bkfrr/"},
            timeout_seconds=12,
            attempts=2,
        )
        frame = pd.read_csv(io.BytesIO(response.content), header=None)
        frame.dropna(axis=1, how="all", inplace=True)
        if len(frame.columns) < 4:
            raise RuntimeError("fdr_csv_columns_missing")
        frame = frame.iloc[:, :4].copy()
        frame.columns = ["date", "FDR001", "FDR007", "FDR014"]
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame["FDR007"] = pd.to_numeric(frame["FDR007"], errors="coerce")
        frame.dropna(subset=["date", "FDR007"], inplace=True)
        frame.sort_values("date", ignore_index=True, inplace=True)
        return frame

    def fetch(self, request: BridgeFetchRequest) -> AdapterResult:
        try:
            frame = self._query_fdr007()
        except Exception as exc:
            return _unavailable(
                "CHINAMONEY_AKSHARE", f"dr007_source_error:{type(exc).__name__}"
            )
        if frame.empty:
            return _unavailable("CHINAMONEY_AKSHARE", "dr007_empty")

        observations: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            obs_date = row["date"]
            if not isinstance(obs_date, date):
                continue
            if not request.start_date <= obs_date <= request.end_date:
                continue
            value = _as_float(row["FDR007"])
            if value is None:
                continue
            # FDR007 is explicitly treated as a fixing proxy for DR007, not as the raw
            # full-day weighted DR007 series.  Noon is a conservative PIT availability bound.
            release = _release_ts(obs_date, 12, 0)
            if release > request.as_of:
                continue
            observations.append(
                {
                    "observation_date": obs_date,
                    "value": value,
                    "release_ts": release,
                    "source_id": "CHINAMONEY_AKSHARE",
                    "revision_vintage": obs_date.isoformat(),
                    "definition_version": "DR007_FDR007_FIXING_PROXY_FREE_V3",
                    "metadata": {
                        "benchmark": "DR007",
                        "upstream_benchmark": "FDR007",
                        "measure_type": "depository_repo_fixing_proxy",
                        "proxy": True,
                        "unit": "percent",
                        "upstream": "ChinaMoney fdr-chrt.csv",
                        "source_semantics": (
                            "FDR007 fixing proxy derived from depository-institution repo "
                            "transactions; FR007 is never substituted"
                        ),
                    },
                }
            )
        observations.sort(key=lambda x: x["observation_date"])
        if not observations:
            return _unavailable("CHINAMONEY_AKSHARE", "dr007_no_pit_observation")
        return AdapterResult(
            "CHINAMONEY_AKSHARE",
            "VALID",
            observations,
            ["dr007_free_source_uses_fdr007_fixing_proxy"],
        )


INDEX_CODES = {
    "CSI300": "000300",
    "CSI_A500": "000510",
    "CSI500": "000905",
    "CSI1000": "000852",
    "CHINEXT": "399006",
    "STAR50": "000688",
}


class EarningsRevisionAdapter(BaseAdapter):
    capability = "equity_index.earnings_revision"
    EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

    def __init__(self, store: BridgeStore, http: FreeSourceClient) -> None:
        super().__init__(store, http)
        self._cache_lock = threading.Lock()
        self._forecast_cache: pd.DataFrame | None = None
        self._forecast_cache_until: datetime | None = None
        self._forecast_error_until: datetime | None = None
        self._forecast_error: str | None = None
        self._constituent_cache: dict[str, tuple[datetime, set[str]]] = {}

    @staticmethod
    def _normalize_code(value: Any) -> str | None:
        text = str(value).strip()
        m = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
        if m:
            return m.group(1)
        digits = re.sub(r"\D", "", text)
        if digits:
            return digits[-6:].zfill(6)
        return None

    @staticmethod
    def _forecast_column(frame: pd.DataFrame, as_of: datetime) -> str | None:
        year_cols: list[tuple[int, str]] = []
        for c in frame.columns:
            m = re.match(r"^(\d{4})预测每股收益$", str(c).strip())
            if m:
                year_cols.append((int(m.group(1)), str(c)))
        if not year_cols:
            return None
        future = sorted((y, c) for y, c in year_cols if y >= as_of.year)
        return (future[0] if future else sorted(year_cols)[-1])[1]

    def _eastmoney_page(self, page: int, page_size: int = 5000) -> dict[str, Any]:
        params = {
            "reportName": "RPT_WEB_RESPREDICT",
            "columns": "WEB_RESPREDICT",
            "pageNumber": str(page),
            "pageSize": str(page_size),
            "sortTypes": "-1",
            "sortColumns": "RATING_ORG_NUM",
            "p": str(page),
            "pageNo": str(page),
            "pageNum": str(page),
        }
        url = f"{self.EASTMONEY_URL}?{urlencode(params)}"
        response = self.http.get(
            url,
            cache_seconds=30 * 60,
            headers={"Referer": "https://data.eastmoney.com/report/profitforecast.jshtml"},
            timeout_seconds=12,
            attempts=1,
        )
        payload = json.loads(response.content.decode("utf-8-sig"))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("eastmoney_forecast_result_missing")
        return result

    def _load_forecast_frame_uncached(self) -> pd.DataFrame:
        first = self._eastmoney_page(1)
        rows = list(first.get("data") or [])
        pages = max(1, int(first.get("pages") or 1))
        # Eastmoney currently paginates RPT_WEB_RESPREDICT even when pageSize=5000;
        # six pages is normal in production. Keep a generous but finite upper bound so
        # an upstream pagination bug can never make this free-source path unbounded.
        max_pages = max(1, int(os.getenv("WIDEGOLD_EARNINGS_REV_MAX_PAGES", "20")))
        if pages > max_pages:
            raise RuntimeError(
                f"eastmoney_forecast_pages_limit_exceeded:{pages}>{max_pages}"
            )
        for page in range(2, pages + 1):
            rows.extend(self._eastmoney_page(page).get("data") or [])
        if not rows:
            raise RuntimeError("eastmoney_forecast_empty")

        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = self._normalize_code(row.get("SECURITY_CODE") or row.get("SECUCODE"))
            if not code:
                continue
            item: dict[str, Any] = {"代码": code}
            for idx in range(1, 5):
                year = row.get(f"YEAR{idx}")
                eps = _as_float(row.get(f"EPS{idx}"))
                try:
                    year_int = int(float(year))
                except (TypeError, ValueError):
                    continue
                item[f"{year_int}预测每股收益"] = eps
            normalized.append(item)
        if not normalized:
            raise RuntimeError("eastmoney_forecast_rows_unusable")
        return pd.DataFrame(normalized)

    def _forecast_frame(self) -> pd.DataFrame:
        now = datetime.now(UTC)
        with self._cache_lock:
            if (
                self._forecast_cache is not None
                and self._forecast_cache_until is not None
                and now < self._forecast_cache_until
            ):
                return self._forecast_cache.copy()
            if self._forecast_error_until is not None and now < self._forecast_error_until:
                raise RuntimeError(self._forecast_error or "eastmoney_forecast_backoff")

        try:
            frame = self._load_forecast_frame_uncached()
        except Exception as exc:
            with self._cache_lock:
                self._forecast_error = f"{type(exc).__name__}:{exc}"
                self._forecast_error_until = now + timedelta(minutes=2)
            raise

        with self._cache_lock:
            self._forecast_cache = frame.copy()
            self._forecast_cache_until = now + timedelta(minutes=30)
            self._forecast_error_until = None
            self._forecast_error = None
        return frame

    def _constituents(self, asset_id: str) -> set[str]:
        now = datetime.now(UTC)
        cached = self._constituent_cache.get(asset_id)
        if cached is not None and now < cached[0]:
            return set(cached[1])

        symbol = INDEX_CODES[asset_id]
        if asset_id == "CHINEXT":
            params = urlencode({"indexcode": symbol})
            url = f"https://www.cnindex.com.cn/sample-detail/download-history?{params}"
            response = self.http.get(
                url,
                cache_seconds=24 * 3600,
                headers={"Referer": "https://www.cnindex.com.cn/"},
                timeout_seconds=12,
                attempts=1,
            )
            frame = pd.read_excel(io.BytesIO(response.content), dtype=str)
            code_col = _first_existing(
                list(frame.columns), ["样本代码", "证券代码", "成份券代码", "代码"]
            )
        else:
            url = (
                "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/"
                f"autofile/cons/{symbol}cons.xls"
            )
            response = self.http.get(
                url,
                cache_seconds=24 * 3600,
                headers={"Referer": "https://www.csindex.com.cn/"},
                timeout_seconds=12,
                attempts=1,
            )
            frame = pd.read_excel(io.BytesIO(response.content), dtype=str)
            code_col = None
            for column in frame.columns:
                label = str(column).replace(" ", "").lower()
                if (
                    "成份券代码" in label
                    or "成分券代码" in label
                    or "constituentcode" in label
                    or label in {"证券代码", "代码"}
                ):
                    code_col = str(column)
                    break

        if code_col is None:
            raise RuntimeError(f"constituent_code_column_missing:{asset_id}")
        codes = {
            code
            for value in frame[code_col].dropna().tolist()
            if (code := self._normalize_code(value)) is not None
        }
        if not codes:
            raise RuntimeError(f"constituents_empty:{asset_id}")
        self._constituent_cache[asset_id] = (now + timedelta(hours=24), set(codes))
        return codes

    def fetch(self, request: BridgeFetchRequest) -> AdapterResult:
        if request.asset_id not in INDEX_CODES:
            return _unavailable("EASTMONEY_AKSHARE", "earnings_revision_asset_unsupported")
        try:
            # Forecast is shared across all six index scopes; load it first so a transient
            # Eastmoney failure is negatively cached and subsequent scopes fail fast.
            frame = self._forecast_frame()
            constituents = self._constituents(request.asset_id)
        except Exception as exc:
            return _unavailable(
                "EASTMONEY_AKSHARE", f"earnings_revision_source_error:{type(exc).__name__}"
            )
        if frame.empty or not constituents or "代码" not in frame.columns:
            return _unavailable("EASTMONEY_AKSHARE", "earnings_revision_source_empty")
        forecast_col = self._forecast_column(frame, request.as_of)
        if forecast_col is None:
            return _unavailable("EASTMONEY_AKSHARE", "earnings_revision_forecast_column_missing")

        filtered = frame[frame["代码"].astype(str).str.zfill(6).isin(constituents)].copy()
        current_values: dict[str, float] = {}
        for _, row in filtered.iterrows():
            value = _as_float(row[forecast_col])
            if value is not None:
                current_values[str(row["代码"]).zfill(6)] = value
        if not current_values:
            return _unavailable("EASTMONEY_AKSHARE", "earnings_revision_no_constituent_forecasts")

        collected_at = datetime.now(UTC)
        fetched_snapshot_date = min(request.end_date, collected_at.astimezone(SHANGHAI).date())
        self.store.save_earnings_snapshot(
            fetched_snapshot_date, collected_at, request.asset_id, current_values
        )

        current = self.store.load_latest_earnings_snapshot(request.asset_id, request.as_of)
        window_days = int(
            request.params.get("window_days")
            or os.getenv("WIDEGOLD_EARNINGS_REV_WINDOW_DAYS", "7")
        )
        if current is None:
            return _unavailable(
                "EASTMONEY_AKSHARE",
                "earnings_revision_history_warming:no_snapshot_available_at_as_of",
            )
        snapshot_date, current_collected_at, usable_values = current
        prior = self.store.load_earnings_snapshot(
            request.asset_id, snapshot_date - timedelta(days=window_days)
        )
        if prior is None:
            return _unavailable(
                "EASTMONEY_AKSHARE",
                f"earnings_revision_history_warming:need_at_least_{window_days}_days",
            )
        prior_date, _, old_values = prior
        common = sorted(set(usable_values) & set(old_values))
        min_coverage = float(os.getenv("WIDEGOLD_EARNINGS_REV_MIN_COVERAGE", "0.50"))
        coverage = len(common) / max(1, len(constituents))
        if coverage < min_coverage:
            return _unavailable(
                "EASTMONEY_AKSHARE",
                f"earnings_revision_coverage_too_low:{coverage:.3f}",
            )

        eps = 1e-12
        up = down = unchanged = 0
        for code in common:
            delta = usable_values[code] - old_values[code]
            threshold = max(abs(old_values[code]) * 0.005, eps)
            if delta > threshold:
                up += 1
            elif delta < -threshold:
                down += 1
            else:
                unchanged += 1
        breadth = 100.0 * (up - down) / max(1, up + down + unchanged)
        return AdapterResult(
            "EASTMONEY_AKSHARE",
            "VALID",
            [
                {
                    "observation_date": snapshot_date,
                    "value": float(np.clip(breadth, -100.0, 100.0)),
                    "release_ts": current_collected_at,
                    "source_id": "EASTMONEY_AKSHARE",
                    "asset_id": request.asset_id,
                    "revision_vintage": current_collected_at.isoformat(),
                    "definition_version": "EARNINGS_REV_BREADTH_FREE_V2",
                    "metadata": {
                        "methodology": "revision_breadth",
                        "window_days": window_days,
                        "prior_snapshot_date": prior_date.isoformat(),
                        "coverage": round(coverage, 4),
                        "up": up,
                        "down": down,
                        "unchanged": unchanged,
                        "forecast_column": forecast_col,
                        "source_semantics": "collector_observed_snapshot",
                        "transport": "bounded_direct_http",
                    },
                }
            ],
            [],
        )


class ForeignActivityAdapter(BaseAdapter):
    capability = "china_equity.foreign_activity"
    EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

    def fetch(self, request: BridgeFetchRequest) -> AdapterResult:
        # Northbound daily net-buy disclosure stopped in 2024.  The former AKShare
        # RPT_MUTUAL_STOCK_NORTHSTA wrapper is now frequently broken.  We make one bounded
        # direct request only; if the public quarterly holding dataset is unavailable we
        # degrade explicitly rather than substituting turnover as directional flow.
        params = {
            "sortColumns": "TRADE_DATE,ADD_MARKET_CAP",
            "sortTypes": "-1,-1",
            "pageSize": "5000",
            "pageNumber": "1",
            "reportName": "RPT_MUTUAL_STOCK_NORTHSTA",
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "filter": '(INTERVAL_TYPE="Q")',
        }
        url = f"{self.EASTMONEY_URL}?{urlencode(params)}"
        try:
            response = self.http.get(
                url,
                cache_seconds=6 * 3600,
                headers={"Referer": "https://data.eastmoney.com/hsgtcg/list.html"},
                timeout_seconds=8,
                attempts=1,
            )
            payload = json.loads(response.content.decode("utf-8-sig"))
            result = payload.get("result")
            if not isinstance(result, dict) or not result.get("data"):
                return _unavailable(
                    "EASTMONEY_HKEX_PROXY",
                    "foreign_activity_free_quarterly_source_unavailable",
                )
            frame = pd.DataFrame(result["data"])
        except Exception as exc:
            return _unavailable(
                "EASTMONEY_HKEX_PROXY",
                f"foreign_activity_source_unavailable:{type(exc).__name__}",
            )

        if "TRADE_DATE" not in frame.columns or "ADD_MARKET_CAP" not in frame.columns:
            return _unavailable("EASTMONEY_HKEX_PROXY", "foreign_activity_columns_missing")
        dates = pd.to_datetime(frame["TRADE_DATE"], errors="coerce")
        if dates.dropna().empty:
            return _unavailable("EASTMONEY_HKEX_PROXY", "foreign_activity_date_missing")
        latest = dates.max()
        latest_mask = dates.dt.date == latest.date()
        values = pd.to_numeric(frame.loc[latest_mask, "ADD_MARKET_CAP"], errors="coerce").dropna()
        if values.empty:
            return _unavailable("EASTMONEY_HKEX_PROXY", "foreign_activity_values_missing")

        obs_date = latest.date()
        if not request.start_date <= obs_date <= request.end_date:
            return _unavailable("EASTMONEY_HKEX_PROXY", "foreign_activity_outside_request_window")
        # Eastmoney's legacy ranking field is a signed holding-value change. Keep the unit
        # conversion used by the original adapter (10k CNY -> CNY billion).
        signed_billion_cny = float(values.sum()) / 100000.0
        release = _release_ts(obs_date, 23, 59)
        if release > request.as_of:
            return _unavailable("EASTMONEY_HKEX_PROXY", "foreign_activity_release_after_as_of")
        self.store.save_foreign_snapshot(
            obs_date,
            datetime.now(UTC),
            signed_billion_cny,
            {"rows": int(latest_mask.sum()), "field": "ADD_MARKET_CAP"},
        )
        return AdapterResult(
            "EASTMONEY_HKEX_PROXY",
            "VALID",
            [
                {
                    "observation_date": obs_date,
                    "value": signed_billion_cny,
                    "release_ts": release,
                    "source_id": "EASTMONEY_HKEX_PROXY",
                    "revision_vintage": release.isoformat(),
                    "definition_version": "FOREIGN_ACTIVITY_FREE_V2",
                    "metadata": {
                        "directionality": "signed",
                        "measure_type": "quarterly_holding_value_change",
                        "window": "quarter",
                        "unit": "CNY_billion",
                        "source_semantics": (
                            "free quarterly holding-value-change proxy; turnover is not used"
                        ),
                    },
                }
            ],
            ["foreign_activity_free_source_is_quarterly_not_daily"],
        )


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _parse_wgc_date(text: str, marker: str) -> date | None:
    # Supports "Data as of 30 June, 2026" and publication dates such as "30 July, 2026".
    section = text
    if marker:
        idx = text.lower().find(marker.lower())
        if idx >= 0:
            section = text[idx : idx + 200]
    m = re.search(
        r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December),?\s+(\d{4})",
        section,
        flags=re.I,
    )
    if not m:
        return None
    return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))


def _discover_xlsx(html: str, base_url: str) -> str | None:
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I)
    xlsx = [h for h in hrefs if ".xlsx" in h.lower()]
    # Prefer English table workbooks.
    xlsx.sort(key=lambda h: ("EN" not in h.upper(), len(h)))
    return urljoin(base_url, xlsx[0]) if xlsx else None


def _latest_numeric_on_labeled_row(frame: pd.DataFrame, labels: list[str]) -> float | None:
    for _, row in frame.iterrows():
        cells = [str(v).strip() if not pd.isna(v) else "" for v in row.tolist()]
        joined = " | ".join(cells).lower()
        if not any(label.lower() in joined for label in labels):
            continue
        nums: list[float] = []
        for value in row.tolist()[1:]:
            number = _as_float(value)
            if number is not None:
                nums.append(number)
        if nums:
            return nums[-1]
    return None


class _WgcTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._in_row = False
        self._in_cell = False
        self._row: list[str] = []
        self._cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._in_row = True
            self._row = []
        elif self._in_row and tag.lower() in {"td", "th"}:
            self._in_cell = True
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._in_cell and tag in {"td", "th"}:
            text = " ".join(" ".join(self._cell).split())
            self._row.append(text)
            self._in_cell = False
            self._cell = []
        elif self._in_row and tag == "tr":
            if self._row:
                self.rows.append(self._row)
            self._in_row = False
            self._row = []


def _wgc_quarter_table(html: str) -> tuple[date, pd.DataFrame]:
    parser = _WgcTableParser()
    parser.feed(html)
    header_index = -1
    quarter_columns: list[tuple[int, int, int]] = []
    quarter_re = re.compile(r"^Q([1-4])[’'](\d{2})$")
    for row_idx, row in enumerate(parser.rows):
        found: list[tuple[int, int, int]] = []
        for col_idx, cell in enumerate(row):
            m = quarter_re.match(cell.replace(" ", ""))
            if m:
                found.append((col_idx, int(m.group(1)), 2000 + int(m.group(2))))
        if len(found) >= 2:
            header_index = row_idx
            quarter_columns = found
            break
    if header_index < 0 or not quarter_columns:
        raise RuntimeError("wgc_quarter_header_not_found")

    col_idx, quarter, year = quarter_columns[-1]
    quarter_end = {
        1: date(year, 3, 31),
        2: date(year, 6, 30),
        3: date(year, 9, 30),
        4: date(year, 12, 31),
    }[quarter]
    output: list[list[Any]] = []
    for row in parser.rows[header_index + 1 :]:
        if len(row) <= col_idx:
            continue
        label = row[0].strip()
        if not label:
            continue
        raw = row[col_idx].replace(",", "").replace("−", "-").strip()
        if raw in {"", "-", "–", "—"}:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        output.append([label, value])
    if not output:
        raise RuntimeError("wgc_quarter_table_empty")
    return quarter_end, pd.DataFrame(output)


def _wgc_report_candidates(as_of: datetime) -> list[str]:
    d = as_of.astimezone(UTC).date()
    y, m = d.year, d.month
    slugs: list[str]
    if m >= 8:
        slugs = [f"gold-demand-trends-q2-{y}", f"gold-demand-trends-q1-{y}"]
    elif m >= 5:
        slugs = [f"gold-demand-trends-q1-{y}", f"gold-demand-trends-full-year-{y - 1}"]
    elif m >= 2:
        slugs = [f"gold-demand-trends-full-year-{y - 1}", f"gold-demand-trends-q3-{y - 1}"]
    else:
        slugs = [f"gold-demand-trends-q3-{y - 1}", f"gold-demand-trends-q2-{y - 1}"]
    return [f"https://www.gold.org/goldhub/research/gold-demand-trends/{slug}" for slug in slugs]


class WGCWorkbook:
    """Load WGC quarterly supply/demand with an HTML-first free-source path.

    WGC's linked XLSX is sometimes protected by CDN rules even when the public report page is
    readable.  The report itself contains the authoritative quarterly table, so the bridge parses
    that HTML first and only returns an observation whose publication date is inside the PIT as_of.
    """

    def __init__(self, client: FreeSourceClient) -> None:
        self.client = client

    def load(self, as_of: datetime) -> tuple[date, date, list[pd.DataFrame], str]:
        configured = os.getenv("WIDEGOLD_WGC_REPORT_URL", "").strip()
        candidates = [configured] if configured else _wgc_report_candidates(as_of)
        errors: list[str] = []
        for report_url in candidates:
            try:
                page = self.client.get(
                    report_url,
                    cache_seconds=24 * 3600,
                    headers={
                        "Referer": "https://www.gold.org/goldhub/research/gold-demand-trends",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                    timeout_seconds=12,
                    attempts=1,
                )
                html = page.text
                visible = re.sub(r"<[^>]+>", " ", html)
                visible = " ".join(visible.split())
                publish_date = _parse_wgc_date(visible, "")
                obs_date, frame = _wgc_quarter_table(html)
                if publish_date is None:
                    raise RuntimeError("wgc_publish_date_missing")
                release = datetime.combine(publish_date, dt_time(23, 59, 59), tzinfo=UTC)
                if release > as_of.astimezone(UTC):
                    errors.append(f"future_report:{report_url}")
                    continue
                return obs_date, publish_date, [frame], report_url
            except Exception as exc:
                errors.append(f"{type(exc).__name__}:{report_url}")
        raise RuntimeError("wgc_html_sources_failed:" + ";".join(errors))


class CentralBankGoldAdapter(BaseAdapter):
    capability = "gold.global_central_bank_demand"

    def fetch(self, request: BridgeFetchRequest) -> AdapterResult:
        try:
            obs_date, publish_date, frames, xlsx_url = WGCWorkbook(self.http).load(request.as_of)
        except Exception as exc:
            return _unavailable("WGC", f"wgc_workbook_error:{type(exc).__name__}")
        value: float | None = None
        for frame in frames:
            value = _latest_numeric_on_labeled_row(
                frame,
                ["central banks & other inst", "central banks and other inst", "central banks"],
            )
            if value is not None:
                break
        if value is None:
            return _unavailable("WGC", "wgc_central_bank_row_not_found")
        release = datetime.combine(publish_date, dt_time(23, 59, 59), tzinfo=UTC)
        if release > request.as_of.astimezone(UTC):
            return _unavailable("WGC", "wgc_release_after_as_of")
        if obs_date < request.start_date or obs_date > request.end_date:
            return _unavailable("WGC", "wgc_observation_outside_request_window")
        return AdapterResult(
            "WGC",
            "VALID",
            [
                {
                    "observation_date": obs_date,
                    "value": value,
                    "release_ts": release,
                    "source_id": "WGC",
                    "revision_vintage": publish_date.isoformat(),
                    "definition_version": "WGC_CENTRAL_BANK_FREE_V1",
                    "metadata": {
                        "aggregation_scope": "quarterly_net_purchases",
                        "unit": "tonnes",
                        "source_url": xlsx_url,
                    },
                }
            ],
            [],
        )


class GoldSupplyDemandAdapter(BaseAdapter):
    capability = "gold.supply_fabrication_demand"

    COMPONENT_LABELS = {
        "mine_supply": ["mine production"],
        "producer_hedging": ["net producer hedging"],
        "recycling": ["recycled gold", "recycling"],
        "jewellery": ["jewellery fabrication", "jewelry fabrication", "jewellery"],
        "technology": ["technology"],
        "investment": ["investment"],
        "central_banks": ["central banks & other inst", "central banks"],
    }

    def fetch(self, request: BridgeFetchRequest) -> AdapterResult:
        try:
            obs_date, publish_date, frames, xlsx_url = WGCWorkbook(self.http).load(request.as_of)
        except Exception as exc:
            return _unavailable("WGC", f"wgc_workbook_error:{type(exc).__name__}")
        components: dict[str, float] = {}
        for name, labels in self.COMPONENT_LABELS.items():
            for frame in frames:
                value = _latest_numeric_on_labeled_row(frame, labels)
                if value is not None:
                    components[name] = value
                    break
        required = {"mine_supply", "recycling", "jewellery", "technology", "investment", "central_banks"}
        if not required.issubset(components):
            missing = sorted(required - set(components))
            return _unavailable("WGC", f"wgc_components_missing:{','.join(missing)}")

        supply = components["mine_supply"] + components["recycling"] + components.get("producer_hedging", 0.0)
        demand = (
            components["jewellery"]
            + components["technology"]
            + components["investment"]
            + components["central_banks"]
        )
        pressure = 100.0 * (demand - supply) / max(abs(demand) + abs(supply), 1e-9)
        release = datetime.combine(publish_date, dt_time(23, 59, 59), tzinfo=UTC)
        if release > request.as_of.astimezone(UTC):
            return _unavailable("WGC", "wgc_release_after_as_of")
        if obs_date < request.start_date or obs_date > request.end_date:
            return _unavailable("WGC", "wgc_observation_outside_request_window")
        component_names = sorted(components)
        return AdapterResult(
            "WGC",
            "VALID",
            [
                {
                    "observation_date": obs_date,
                    "value": float(np.clip(pressure, -100.0, 100.0)),
                    "release_ts": release,
                    "source_id": "WGC",
                    "revision_vintage": publish_date.isoformat(),
                    "definition_version": "WGC_STRUCTURAL_PRESSURE_FREE_V1",
                    "metadata": {
                        "methodology": "structural_pressure_v1",
                        "components": component_names,
                        "component_values_tonnes": components,
                        "supply_tonnes": supply,
                        "demand_tonnes": demand,
                        "source_url": xlsx_url,
                    },
                }
            ],
            [],
        )
