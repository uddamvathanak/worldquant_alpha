from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class RunRecord:
    run_id: str
    run_at: str
    hypothesis_id: str | None
    title: str | None
    expression: str
    dataset: str
    notes: str
    metrics: dict[str, Any]
    settings: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    why_worked: str = ""
    why_failed: str = ""
    economic_intuition: str = ""
    next_step: str = ""


class ExperimentStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                  run_id TEXT PRIMARY KEY,
                  run_at TEXT NOT NULL,
                  hypothesis_id TEXT,
                  title TEXT,
                  expression TEXT NOT NULL,
                  dataset TEXT NOT NULL,
                  notes TEXT,
                  status TEXT NOT NULL DEFAULT '',
                  why_worked TEXT NOT NULL DEFAULT '',
                  why_failed TEXT NOT NULL DEFAULT '',
                  economic_intuition TEXT NOT NULL DEFAULT '',
                  next_step TEXT NOT NULL DEFAULT '',
                  settings_json TEXT NOT NULL DEFAULT '{}',
                  metrics_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_metrics (
                  run_id TEXT NOT NULL,
                  date TEXT NOT NULL,
                  ic REAL,
                  pnl REAL,
                  turnover REAL,
                  coverage INTEGER,
                  PRIMARY KEY (run_id, date)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_run_at ON runs(run_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_run_id ON daily_metrics(run_id)"
            )
            self._ensure_column(conn, "runs", "settings_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "runs", "status", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "runs", "why_worked", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "runs", "why_failed", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(
                conn,
                "runs",
                "economic_intuition",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(conn, "runs", "next_step", "TEXT NOT NULL DEFAULT ''")
            conn.commit()

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        ddl: str,
    ) -> None:
        columns = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def log_run(self, record: RunRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                  run_id, run_at, hypothesis_id, title, expression, dataset, notes,
                  status, why_worked, why_failed, economic_intuition, next_step,
                  settings_json, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.run_at or _utc_now_iso(),
                    record.hypothesis_id,
                    record.title,
                    record.expression,
                    record.dataset,
                    record.notes,
                    record.status,
                    record.why_worked,
                    record.why_failed,
                    record.economic_intuition,
                    record.next_step,
                    json.dumps(record.settings or {}, ensure_ascii=True),
                    json.dumps(record.metrics, ensure_ascii=True),
                ),
            )
            conn.commit()

    def list_runs(self, limit: int = 20) -> pd.DataFrame:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, run_at, hypothesis_id, title, expression, dataset, notes,
                       status, why_worked, why_failed, economic_intuition, next_step,
                       metrics_json
                     , settings_json
                FROM runs
                ORDER BY run_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        records: list[dict[str, Any]] = []
        for row in rows:
            raw = dict(row)
            settings = json.loads(raw.pop("settings_json") or "{}")
            metrics = json.loads(raw.pop("metrics_json"))
            raw.update({f"setting_{k}": v for k, v in settings.items()})
            raw.update({f"metric_{k}": v for k, v in metrics.items()})
            records.append(raw)
        return pd.DataFrame(records)

    def get_daily(self, run_id: str) -> pd.DataFrame:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT date, ic, pnl, turnover, coverage
                FROM daily_metrics
                WHERE run_id = ?
                ORDER BY date
                """,
                (run_id,),
            ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, run_at, hypothesis_id, title, expression, dataset, notes,
                       status, why_worked, why_failed, economic_intuition, next_step,
                       settings_json, metrics_json
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if not row:
            return None
        raw = dict(row)
        raw["settings"] = json.loads(raw.pop("settings_json") or "{}")
        raw["metrics"] = json.loads(raw.pop("metrics_json") or "{}")
        return raw
