from __future__ import annotations

from math import log, tanh
from statistics import median

from widegold.schemas.indicators import IndicatorObservation


def _clip(value: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _mad(values: list[float]) -> float:
    if not values:
        return 0.0
    med = median(values)
    return median([abs(v - med) for v in values])


def robust_score(value: float, history: list[float]) -> float:
    if len(history) < 3:
        return 0.0
    med = median(history)
    mad = _mad(history)
    if mad < 1e-12:
        # Fall back to a scale tied to the level so flat histories do not explode.
        scale = max(abs(med) * 0.01, 1e-6)
    else:
        scale = 1.4826 * mad
    z = (value - med) / scale
    return _clip(100.0 * tanh(z / 2.0))


def _values(series: list[IndicatorObservation]) -> list[float]:
    return [float(row.value) for row in sorted(series, key=lambda x: x.observation_date)]


def level_robust(series: list[IndicatorObservation]) -> float | None:
    values = _values(series)
    if len(values) < 3:
        return None
    window = values[-120:]
    return robust_score(window[-1], window[:-1] or window)


def change_robust(series: list[IndicatorObservation], lag: int) -> float | None:
    """Direction-preserving robust state of an absolute lag change.

    A conventional median-centered z-score answers "is today's change unusual?" but
    incorrectly maps a persistent steady decline to ~0 once that decline becomes the
    rolling median. Factor state needs the direction of the current change as well.
    We therefore estimate a robust *scale* from historical absolute changes and center
    at zero. Separate surprise features can be added later when unusualness is desired.
    """
    values = _values(series)
    if len(values) <= lag:
        return None
    deltas = [values[i] - values[i - lag] for i in range(lag, len(values))]
    if not deltas:
        return None
    history = deltas[-120:]
    abs_history = [abs(x) for x in history[:-1]] or [abs(history[-1])]
    scale = median(abs_history) * 1.4826
    if scale < 1e-12:
        scale = max(abs(values[-lag - 1]) * 0.01, 1e-6)
    return _clip(100.0 * tanh((history[-1] / scale) / 2.0))


def return_robust(series: list[IndicatorObservation], lag: int) -> float | None:
    values = _values(series)
    if len(values) <= lag or values[-lag - 1] <= 0 or values[-1] <= 0:
        return None
    current = log(values[-1] / values[-lag - 1])
    # Momentum should preserve the sign of the current return. A robust percentile of rolling
    # returns can incorrectly mark a steadily rising series as negative merely because its
    # percentage return is slowly decelerating. Scale grows with the horizon.
    scale = 0.08 if lag <= 20 else 0.15 if lag <= 60 else 0.25
    return _clip(100.0 * tanh(current / scale))


def pmi_state(series: list[IndicatorObservation]) -> float | None:
    values = _values(series)
    if not values:
        return None
    latest = values[-1]
    level = _clip(100.0 * tanh((latest - 50.0) / 4.0))
    if len(values) < 2:
        return level
    trend = _clip(100.0 * tanh((latest - values[-2]) / 2.0))
    return 0.65 * level + 0.35 * trend



def yoy_pct_robust(series: list[IndicatorObservation], lag: int = 12) -> float | None:
    """Robust score of year-over-year percentage change for positive flow/level series.

    Intended for monthly levels such as total social-financing increment. It preserves the
    economic meaning "higher/lower than the same month last year" while avoiding the severe
    seasonality of comparing adjacent months.
    """
    values = _values(series)
    if len(values) <= lag:
        return None
    base = values[-lag - 1]
    if abs(base) < 1e-12:
        return None
    current = 100.0 * (values[-1] / base - 1.0)
    historical: list[float] = []
    for i in range(lag, len(values) - 1):
        prev = values[i - lag]
        if abs(prev) >= 1e-12:
            historical.append(100.0 * (values[i] / prev - 1.0))
    scale_hist = historical[-60:]
    if len(scale_hist) < 3:
        # A conservative fallback scale in percentage points.
        return _clip(100.0 * tanh(current / 20.0))
    abs_hist = [abs(x) for x in scale_hist]
    scale = max(median(abs_hist) * 1.4826, 5.0)
    return _clip(100.0 * tanh((current / scale) / 2.0))

def feature_score(feature: str, series: list[IndicatorObservation]) -> float | None:
    if feature == "level_robust":
        return level_robust(series)
    if feature == "pmi_state":
        return pmi_state(series)
    if feature.startswith("change_"):
        return change_robust(series, int(feature.split("_", 1)[1]))
    if feature.startswith("return_"):
        return return_robust(series, int(feature.split("_", 1)[1]))
    if feature.startswith("yoy_pct_"):
        return yoy_pct_robust(series, int(feature.rsplit("_", 1)[1]))
    raise ValueError(f"Unknown indicator feature: {feature}")
