from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timezone
from math import isnan

import numpy as np
import pandas as pd

from widegold.repositories.factory import repository
from widegold.schemas.replay import (
    CalibrationMetric,
    CalibrationRequest,
    CalibrationResult,
    FactorCalibrationMetric,
    ScoreBucketMetric,
)
from widegold.settings.config import evaluation_config


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(ys) != len(xs):
        return None
    x = pd.Series(xs, dtype="float64").rank(method="average")
    y = pd.Series(ys, dtype="float64").rank(method="average")
    if x.nunique() < 2 or y.nunique() < 2:
        return None
    value = float(x.corr(y, method="pearson"))
    return None if isnan(value) else value


def _forward_returns(history, base_date, horizons: list[int]) -> dict[int, float]:
    """Calculate T+N returns using the next N *available trading observations*.

    Price observations are sorted by observation_date. The base price is the latest observation
    on or before the analysis date; T+1 is the next available price observation, not the next
    calendar day. This naturally handles weekends and market holidays.
    """
    rows = sorted(history, key=lambda x: x.observation_date)
    base_idx = None
    for idx, row in enumerate(rows):
        if row.observation_date <= base_date:
            base_idx = idx
        else:
            break
    if base_idx is None:
        return {}
    base = float(rows[base_idx].value)
    if base == 0:
        return {}
    result = {}
    for horizon in horizons:
        target_idx = base_idx + horizon
        if target_idx < len(rows):
            result[horizon] = float(rows[target_idx].value) / base - 1.0
    return result


def run_calibration(request: CalibrationRequest, *, repo=None, evaluation_as_of: datetime | None = None) -> CalibrationResult:
    repo = repo or repository()
    cfg = evaluation_config()
    snapshots = repo.list_snapshots(
        start_date=request.start_date,
        end_date=request.end_date,
        published_only=True,
    )
    # Multiple explicitly-published runs can exist for audit on the same business date. Calibration
    # evaluates the final public decision for that date so a day is not double-weighted merely
    # because an administrator published a corrected run later.
    latest_by_day = {}
    for snapshot in snapshots:
        existing = latest_by_day.get(snapshot.analysis_date)
        if existing is None or snapshot.as_of >= existing.as_of:
            latest_by_day[snapshot.analysis_date] = snapshot
    snapshots = [latest_by_day[key] for key in sorted(latest_by_day)]
    now = evaluation_as_of or datetime.now(timezone.utc)
    price_map = dict(cfg.get("asset_price_indicators", {}))

    # Load each price series once. Evaluation deliberately uses observations that are known by
    # the evaluation timestamp; the historical score itself remains point-in-time frozen.
    price_histories = {
        asset_id: repo.get_indicator_history(indicator_id, as_of=now, limit=10000)
        for asset_id, indicator_id in price_map.items()
    }

    samples: list[dict] = []
    warnings: list[str] = []
    for snapshot in snapshots:
        analysis_day = datetime.fromisoformat(snapshot.analysis_date).date()
        for score in snapshot.assets:
            history = price_histories.get(score.asset_id, [])
            returns = _forward_returns(history, analysis_day, request.horizons)
            if not returns:
                warnings.append(f"future_price_unavailable:{score.asset_id}:{analysis_day.isoformat()}")
                continue
            contribution_map = {row.factor_id: row.after_group_cap for row in score.contributions}
            for horizon, forward_return in returns.items():
                samples.append({
                    "analysis_date": analysis_day,
                    "asset_id": score.asset_id,
                    "horizon": horizon,
                    "score": float(score.score),
                    "signal": (float(score.score) - 50.0) / 50.0,
                    "forward_return": forward_return,
                    "contributions": contribution_map,
                })

    asset_group: dict[tuple[str, int], list[dict]] = defaultdict(list)
    date_group: dict[tuple[object, int], list[dict]] = defaultdict(list)
    factor_group: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    for sample in samples:
        asset_group[(sample["asset_id"], sample["horizon"])].append(sample)
        date_group[(sample["analysis_date"], sample["horizon"])].append(sample)
        for factor_id, contribution in sample["contributions"].items():
            factor_group[(factor_id, sample["horizon"])].append((float(contribution), sample["forward_return"]))

    asset_metrics: list[CalibrationMetric] = []
    for (asset_id, horizon), rows in sorted(asset_group.items()):
        signals = [row["signal"] for row in rows]
        returns = [row["forward_return"] for row in rows]
        directional = [
            (signal > 0 and ret > 0) or (signal < 0 and ret < 0)
            for signal, ret in zip(signals, returns)
            if abs(signal) > 1e-9 and abs(ret) > 1e-12
        ]
        asset_metrics.append(CalibrationMetric(
            asset_id=asset_id,
            horizon=horizon,
            samples=len(rows),
            hit_rate=(sum(directional) / len(directional)) if directional else None,
            mean_forward_return=float(np.mean(returns)) if returns else None,
            signal_return_spearman=_spearman(signals, returns),
        ))

    factor_metrics: list[FactorCalibrationMetric] = []
    for (factor_id, horizon), pairs in sorted(factor_group.items()):
        xs = [x for x, _ in pairs]
        ys = [y for _, y in pairs]
        factor_metrics.append(FactorCalibrationMetric(
            factor_id=factor_id,
            horizon=horizon,
            samples=len(pairs),
            contribution_return_spearman=_spearman(xs, ys),
        ))

    bucket_metrics: list[ScoreBucketMetric] = []
    for bucket in cfg.get("score_buckets", []):
        lo, hi = float(bucket["min"]), float(bucket["max"])
        for horizon in request.horizons:
            returns = [
                row["forward_return"]
                for row in samples
                if row["horizon"] == horizon and lo <= row["score"] < hi
            ]
            bucket_metrics.append(ScoreBucketMetric(
                bucket=str(bucket["name"]),
                horizon=horizon,
                samples=len(returns),
                mean_forward_return=float(np.mean(returns)) if returns else None,
            ))

    rank_ic: dict[int, float | None] = {}
    for horizon in request.horizons:
        daily_ics = []
        for (analysis_day, h), rows in date_group.items():
            if h != horizon or len(rows) < 3:
                continue
            ic = _spearman([row["score"] for row in rows], [row["forward_return"] for row in rows])
            if ic is not None:
                daily_ics.append(ic)
        rank_ic[horizon] = float(np.mean(daily_ics)) if daily_ics else None

    # Deduplicate warning noise while keeping deterministic order.
    warnings = list(dict.fromkeys(warnings))
    result = CalibrationResult(
        calibration_id=request.calibration_id,
        start_date=request.start_date,
        end_date=request.end_date,
        created_at=now,
        horizons=request.horizons,
        evaluation_version=str(cfg.get("version", "1.0.0")),
        published_runs=len(snapshots),
        evaluated_samples=len(samples),
        asset_metrics=asset_metrics,
        factor_metrics=factor_metrics,
        score_buckets=bucket_metrics,
        rank_ic_by_horizon=rank_ic,
        warnings=warnings,
    )
    repo.save_calibration_report(result, requested_by=request.requested_by)
    return result
