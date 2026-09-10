from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from time import monotonic

import pandas as pd

from widegold.data.indicators import parse_date, parse_numeric
from widegold.domain.enums import DataStatus
from widegold.schemas.indicators import IndicatorFetchRequest, IndicatorObservation, IndicatorSeries

_SPOT_CACHE: tuple[float, pd.DataFrame] | None = None
_SPOT_LOCK = Lock()
_SPOT_TTL_SECONDS = 60.0


def _load_akshare():
    try:
        import akshare as ak
    except ImportError as exc:  # pragma: no cover - depends on optional runtime package
        raise RuntimeError("akshare package is not installed") from exc
    return ak


def _a_share_spot(*, force_refresh: bool = False) -> pd.DataFrame:
    global _SPOT_CACHE
    with _SPOT_LOCK:
        now = monotonic()
        if (
            not force_refresh
            and _SPOT_CACHE is not None
            and now - _SPOT_CACHE[0] <= _SPOT_TTL_SECONDS
        ):
            return _SPOT_CACHE[1].copy()
        ak = _load_akshare()
        frame = ak.stock_zh_a_spot_em()
        _SPOT_CACHE = (now, frame.copy())
        return frame


def _obs(request: IndicatorFetchRequest, spec: dict, value: float, obs_date=None, *, status=DataStatus.VALID, metadata=None):
    now = datetime.now(timezone.utc)
    return IndicatorObservation(
        indicator_id=request.indicator_id,
        asset_id=request.asset_id,
        observation_date=obs_date or request.as_of.date(),
        release_ts=request.as_of,
        ingest_ts=now,
        value=float(value),
        source_id=spec.get("source_id", "AKSHARE_DERIVED"),
        revision_vintage=request.as_of.date().isoformat(),
        definition_version=spec.get("definition_version", "1.0.0"),
        status=status,
        metadata=metadata or {},
    )


class AkshareDerivedIndicatorProvider:
    """Small set of auditable market-derived indicators.

    These are deliberately kept in a separate provider because they are calculations over
    raw market snapshots rather than pass-through fields. Each derived definition is versioned
    in ``configs/indicators.yaml`` so future methodology changes do not silently alter history.
    """

    provider_name = "akshare_derived"

    def fetch(self, request: IndicatorFetchRequest, spec: dict) -> IndicatorSeries:
        kind = spec.get("params", {}).get("derived")
        try:
            if kind == "cn_margin_total":
                rows = self._margin_total(request, spec)
            elif kind == "cn_market_breadth":
                rows = self._market_breadth(request, spec)
            elif kind == "cn_risk_appetite":
                rows = self._risk_appetite(request, spec)
            else:
                return IndicatorSeries(
                    indicator_id=request.indicator_id,
                    source_id=spec.get("source_id", "AKSHARE_DERIVED"),
                    status=DataStatus.UNAVAILABLE,
                    warnings=[f"unknown_derived_indicator:{kind}"],
                )
        except Exception as exc:
            return IndicatorSeries(
                indicator_id=request.indicator_id,
                source_id=spec.get("source_id", "AKSHARE_DERIVED"),
                status=DataStatus.UNAVAILABLE,
                warnings=[f"derived_indicator_failed:{kind}:{exc}"],
            )
        return IndicatorSeries(
            indicator_id=request.indicator_id,
            source_id=spec.get("source_id", "AKSHARE_DERIVED"),
            observations=rows,
            status=DataStatus.VALID if rows else DataStatus.UNAVAILABLE,
            warnings=[] if rows else [f"derived_indicator_empty:{kind}"],
        )

    def _margin_total(self, request: IndicatorFetchRequest, spec: dict) -> list[IndicatorObservation]:
        ak = _load_akshare()
        sh = ak.macro_china_market_margin_sh()
        sz = ak.macro_china_market_margin_sz()
        frames: list[pd.DataFrame] = []
        for market, frame in (("SH", sh), ("SZ", sz)):
            if "日期" not in frame.columns or "融资余额" not in frame.columns:
                continue
            part = frame[["日期", "融资余额"]].copy()
            part["date"] = part["日期"].map(parse_date)
            part[market] = part["融资余额"].map(parse_numeric)
            frames.append(part[["date", market]])
        if not frames:
            return []
        merged = frames[0]
        for part in frames[1:]:
            merged = merged.merge(part, on="date", how="outer")
        value_cols = [c for c in ("SH", "SZ") if c in merged.columns]
        merged["total"] = merged[value_cols].fillna(0.0).sum(axis=1)
        rows = [
            _obs(request, spec, row.total, row.date, metadata={"components": value_cols})
            for row in merged.itertuples()
            if request.start_date <= row.date <= request.end_date
        ]
        rows.sort(key=lambda x: x.observation_date)
        return rows

    def _market_breadth(self, request: IndicatorFetchRequest, spec: dict) -> list[IndicatorObservation]:
        frame = _a_share_spot(force_refresh=request.force_refresh)
        if "涨跌幅" not in frame.columns:
            return []
        pct = pd.to_numeric(frame["涨跌幅"], errors="coerce").dropna()
        if pct.empty:
            return []
        adv = int((pct > 0).sum())
        dec = int((pct < 0).sum())
        unchanged = int((pct == 0).sum())
        denom = max(1, adv + dec)
        value = 100.0 * (adv - dec) / denom
        return [_obs(
            request, spec, value,
            metadata={"advancers": adv, "decliners": dec, "unchanged": unchanged, "universe": int(len(pct))},
        )]

    def _risk_appetite(self, request: IndicatorFetchRequest, spec: dict) -> list[IndicatorObservation]:
        frame = _a_share_spot(force_refresh=request.force_refresh)
        if "涨跌幅" not in frame.columns:
            return []
        pct = pd.to_numeric(frame["涨跌幅"], errors="coerce").dropna()
        if pct.empty:
            return []
        adv = int((pct > 0).sum())
        dec = int((pct < 0).sum())
        breadth = 100.0 * (adv - dec) / max(1, adv + dec)
        median_return = float(pct.median())
        # A bounded, transparent C-grade tactical proxy: breadth dominates, while the median
        # stock return contributes a smaller cross-sectional intensity term.
        intensity = max(-100.0, min(100.0, median_return * 20.0))
        value = max(-100.0, min(100.0, 0.70 * breadth + 0.30 * intensity))
        return [_obs(
            request, spec, value,
            metadata={"breadth_component": breadth, "median_return_pct": median_return, "universe": int(len(pct))},
        )]
