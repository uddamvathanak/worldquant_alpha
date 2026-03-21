from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchCache:
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
                CREATE TABLE IF NOT EXISTS candidate_results (
                    evaluation_key TEXT NOT NULL,
                    candidate_signature TEXT NOT NULL,
                    candidate_name TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (evaluation_key, candidate_signature)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_candidate_results_name
                ON candidate_results(candidate_name)
                """
            )
            conn.commit()

    def get(self, *, evaluation_key: str, candidate_signature: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT result_json
                FROM candidate_results
                WHERE evaluation_key = ? AND candidate_signature = ?
                """,
                (evaluation_key, candidate_signature),
            ).fetchone()
        if row is None:
            return None
        return dict(json.loads(str(row["result_json"])))

    def put(
        self,
        *,
        evaluation_key: str,
        candidate_signature: str,
        candidate_name: str,
        result: dict[str, Any],
    ) -> None:
        now_iso = _utc_now_iso()
        payload = json.dumps(result, sort_keys=True, default=str)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO candidate_results (
                    evaluation_key,
                    candidate_signature,
                    candidate_name,
                    result_json,
                    created_at_utc,
                    updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(evaluation_key, candidate_signature) DO UPDATE SET
                    candidate_name = excluded.candidate_name,
                    result_json = excluded.result_json,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    evaluation_key,
                    candidate_signature,
                    candidate_name,
                    payload,
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()


def build_evaluation_key(
    *,
    feed: str,
    latest_completed_date: str,
    train_days: int,
    oos_days: int,
    test_days: int,
    classification_snapshot: str,
    round_trip_cost_bps: float,
) -> str:
    snapshot_value = str(Path(str(classification_snapshot))).replace("\\", "/")
    return json.dumps(
        {
            "feed": str(feed).strip().lower(),
            "latest_completed_date": str(latest_completed_date),
            "train_days": int(train_days),
            "oos_days": int(oos_days),
            "test_days": int(test_days),
            "classification_snapshot": snapshot_value,
            "round_trip_cost_bps": float(round_trip_cost_bps),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
