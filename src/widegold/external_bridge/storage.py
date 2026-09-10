from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator


class BridgeStore:
    """Small durable store for free-source snapshots.

    Earnings-revision and foreign-holding breadth need historical snapshots that free public
    endpoints do not always expose retrospectively.  The bridge stores only normalized snapshots
    and cache metadata in its own Docker volume; WideGold PostgreSQL remains the PIT source of
    truth for accepted observations.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS earnings_snapshot (
                    snapshot_date TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    forecast_value REAL NOT NULL,
                    PRIMARY KEY (snapshot_date, asset_id, stock_code)
                );
                CREATE INDEX IF NOT EXISTS ix_earnings_snapshot_asset_date
                    ON earnings_snapshot(asset_id, snapshot_date);

                CREATE TABLE IF NOT EXISTS foreign_snapshot (
                    snapshot_date TEXT NOT NULL PRIMARY KEY,
                    collected_at TEXT NOT NULL,
                    signed_value REAL NOT NULL,
                    source_payload TEXT
                );

                CREATE TABLE IF NOT EXISTS http_cache (
                    cache_key TEXT PRIMARY KEY,
                    stored_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    content BLOB NOT NULL,
                    content_type TEXT
                );
                """
            )

    def save_earnings_snapshot(
        self,
        snapshot_date: date,
        collected_at: datetime,
        asset_id: str,
        values: dict[str, float],
    ) -> None:
        rows = [
            (
                snapshot_date.isoformat(),
                collected_at.astimezone(timezone.utc).isoformat(),
                asset_id,
                code,
                float(value),
            )
            for code, value in values.items()
        ]
        with self.connect() as con:
            con.executemany(
                """
                INSERT INTO earnings_snapshot
                    (snapshot_date, collected_at, asset_id, stock_code, forecast_value)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date, asset_id, stock_code)
                DO UPDATE SET collected_at=excluded.collected_at,
                              forecast_value=excluded.forecast_value
                """,
                rows,
            )

    def load_latest_earnings_snapshot(
        self, asset_id: str, collected_on_or_before: datetime
    ) -> tuple[date, datetime, dict[str, float]] | None:
        cutoff = collected_on_or_before.astimezone(timezone.utc).isoformat()
        with self.connect() as con:
            row = con.execute(
                """
                SELECT snapshot_date, MAX(collected_at) AS collected_at
                FROM earnings_snapshot
                WHERE asset_id=? AND collected_at<=?
                GROUP BY snapshot_date
                ORDER BY collected_at DESC
                LIMIT 1
                """,
                (asset_id, cutoff),
            ).fetchone()
            if row is None:
                return None
            snapshot_date = date.fromisoformat(row["snapshot_date"])
            collected = datetime.fromisoformat(row["collected_at"])
            rows = con.execute(
                """
                SELECT stock_code, forecast_value
                FROM earnings_snapshot
                WHERE asset_id=? AND snapshot_date=?
                """,
                (asset_id, snapshot_date.isoformat()),
            ).fetchall()
        values = {str(r["stock_code"]): float(r["forecast_value"]) for r in rows}
        return snapshot_date, collected, values

    def load_earnings_snapshot(
        self, asset_id: str, target_on_or_before: date
    ) -> tuple[date, datetime, dict[str, float]] | None:
        with self.connect() as con:
            row = con.execute(
                """
                SELECT MAX(snapshot_date) AS snapshot_date
                FROM earnings_snapshot
                WHERE asset_id=? AND snapshot_date<=?
                """,
                (asset_id, target_on_or_before.isoformat()),
            ).fetchone()
            if row is None or row["snapshot_date"] is None:
                return None
            snapshot_date = date.fromisoformat(row["snapshot_date"])
            rows = con.execute(
                """
                SELECT collected_at, stock_code, forecast_value
                FROM earnings_snapshot
                WHERE asset_id=? AND snapshot_date=?
                """,
                (asset_id, snapshot_date.isoformat()),
            ).fetchall()
        if not rows:
            return None
        collected = datetime.fromisoformat(rows[0]["collected_at"])
        values = {str(r["stock_code"]): float(r["forecast_value"]) for r in rows}
        return snapshot_date, collected, values

    def cache_get(self, key: str, now: datetime) -> tuple[bytes, str | None] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT content, content_type, expires_at FROM http_cache WHERE cache_key=?",
                (key,),
            ).fetchone()
        if row is None or datetime.fromisoformat(row["expires_at"]) < now.astimezone(timezone.utc):
            return None
        return bytes(row["content"]), row["content_type"]

    def cache_put(
        self,
        key: str,
        content: bytes,
        content_type: str | None,
        stored_at: datetime,
        expires_at: datetime,
    ) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO http_cache(cache_key, stored_at, expires_at, content, content_type)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    stored_at=excluded.stored_at,
                    expires_at=excluded.expires_at,
                    content=excluded.content,
                    content_type=excluded.content_type
                """,
                (
                    key,
                    stored_at.astimezone(timezone.utc).isoformat(),
                    expires_at.astimezone(timezone.utc).isoformat(),
                    content,
                    content_type,
                ),
            )

    def save_foreign_snapshot(
        self, snapshot_date: date, collected_at: datetime, signed_value: float, payload: dict
    ) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO foreign_snapshot(snapshot_date, collected_at, signed_value, source_payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(snapshot_date) DO UPDATE SET
                    collected_at=excluded.collected_at,
                    signed_value=excluded.signed_value,
                    source_payload=excluded.source_payload
                """,
                (
                    snapshot_date.isoformat(),
                    collected_at.astimezone(timezone.utc).isoformat(),
                    float(signed_value),
                    json.dumps(payload, ensure_ascii=False, default=str),
                ),
            )
