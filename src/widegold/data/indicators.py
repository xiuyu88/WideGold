from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from widegold.schemas.indicators import IndicatorObservation


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()

    # Common China macro period forms returned by AKShare upstreams.
    for pattern in (
        r"^(\d{4})年(\d{1,2})月",
        r"^(\d{4})[./-](\d{1,2})$",
        r"^(\d{4})(\d{2})$",
    ):
        match = re.match(pattern, text)
        if match:
            return date(int(match.group(1)), int(match.group(2)), 1)
    return pd.to_datetime(text).date()


def parse_numeric(value: Any) -> float:
    """Parse provider numbers without silently changing units.

    Handles common public-data decorations such as commas and percent signs. A percent
    string like ``10.4%`` is stored as 10.4 (percentage points), matching AKShare's
    documented macro column conventions and our indicator registry units.
    """
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric indicator value")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text or text.lower() in {"none", "nan", "null", "--", "-"}:
        raise ValueError("missing numeric value")
    if text.endswith("%"):
        text = text[:-1].strip()
    return float(text)


def _as_frame(frame: Any) -> pd.DataFrame:
    if isinstance(frame, pd.Series):
        name = frame.name or "value"
        result = frame.rename(name).reset_index()
        if result.columns[0] == "index":
            result = result.rename(columns={"index": "date"})
        return result
    if isinstance(frame, pd.DataFrame):
        return frame.copy()
    raise TypeError(f"Unsupported AKShare result type: {type(frame)!r}")


def dataframe_to_observations(
    *,
    indicator_id: str,
    source_id: str,
    frame: pd.DataFrame | pd.Series,
    date_column: str,
    value_column: str,
    release_ts: datetime,
    definition_version: str = "1.0.0",
    where: dict[str, Any] | None = None,
    asset_id: str | None = None,
) -> list[IndicatorObservation]:
    frame = _as_frame(frame)
    if where:
        for key, expected in where.items():
            if key not in frame.columns:
                return []
            frame = frame[frame[key].astype(str) == str(expected)]
    if date_column not in frame.columns or value_column not in frame.columns:
        return []

    now = datetime.now(timezone.utc)
    rows: list[IndicatorObservation] = []
    for _, row in frame[[date_column, value_column]].dropna().iterrows():
        try:
            obs_date = parse_date(row[date_column])
            value = parse_numeric(row[value_column])
        except (TypeError, ValueError, OverflowError):
            continue
        rows.append(
            IndicatorObservation(
                indicator_id=indicator_id,
                asset_id=asset_id,
                observation_date=obs_date,
                # Many aggregated macro endpoints expose observation period but not historical
                # release timestamps. For a live ingestion we conservatively mark the current
                # collection cutoff as the time the system first knew the value. This is safe
                # for future point-in-time replay, although it cannot reconstruct pre-ingestion
                # vintages that the upstream does not expose.
                release_ts=release_ts,
                ingest_ts=now,
                value=value,
                source_id=source_id,
                revision_vintage=release_ts.date().isoformat(),
                definition_version=definition_version,
            )
        )
    rows.sort(key=lambda x: x.observation_date)
    return rows
