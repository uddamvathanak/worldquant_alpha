from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd

from config import ET, UTC


@dataclass(slots=True)
class DailyMetricRecord:
    trade_date: str
    run_id: str
    equity: float
    prev_equity: float
    daily_return: float
    traded_notional: float
    turnover: float
    pnl_gross: float
    pnl_net: float
    cost_bps: float
    sharpe_20: float
    margin_proxy_bps_20: float
    fitness_proxy_20: float
    max_drawdown_to_date: float


class PaperTracker:
    def __init__(self, db_path: Path, logs_dir: Path) -> None:
        self.db_path = db_path
        self.logs_dir = logs_dir
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
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
                    trade_date TEXT NOT NULL,
                    started_at_utc TEXT NOT NULL,
                    started_at_et TEXT NOT NULL,
                    finished_at_utc TEXT,
                    finished_at_et TEXT,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    signal_path TEXT NOT NULL DEFAULT '',
                    config_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS account_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    snapshot_stage TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    timestamp_et TEXT NOT NULL,
                    account_number TEXT,
                    status TEXT,
                    currency TEXT,
                    equity REAL,
                    cash REAL,
                    buying_power REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS position_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    snapshot_stage TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    timestamp_et TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    qty REAL,
                    side TEXT,
                    market_value REAL,
                    signed_market_value REAL,
                    avg_entry_price REAL,
                    unrealized_pl REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    target_stage TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    sector TEXT NOT NULL,
                    score REAL NOT NULL,
                    target_weight REAL NOT NULL,
                    target_notional REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    pass_num INTEGER NOT NULL,
                    event_ts_utc TEXT NOT NULL,
                    event_ts_et TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    target_side TEXT NOT NULL,
                    order_side TEXT NOT NULL,
                    order_notional REAL NOT NULL,
                    order_qty REAL NOT NULL DEFAULT 0,
                    target_weight REAL NOT NULL,
                    target_notional REAL NOT NULL,
                    current_notional REAL NOT NULL,
                    delta_notional REAL NOT NULL,
                    order_id TEXT,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_metrics (
                    trade_date TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    equity REAL NOT NULL,
                    prev_equity REAL NOT NULL,
                    daily_return REAL NOT NULL,
                    traded_notional REAL NOT NULL,
                    turnover REAL NOT NULL,
                    pnl_gross REAL NOT NULL,
                    pnl_net REAL NOT NULL,
                    cost_bps REAL NOT NULL,
                    sharpe_20 REAL NOT NULL,
                    margin_proxy_bps_20 REAL NOT NULL,
                    fitness_proxy_20 REAL NOT NULL,
                    max_drawdown_to_date REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS missed_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL UNIQUE,
                    detected_at_utc TEXT NOT NULL,
                    detected_at_et TEXT NOT NULL,
                    reason TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_trade_date ON runs(trade_date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_orders_run_id ON order_events(run_id)"
            )
            self._ensure_column(
                conn,
                "order_events",
                "order_qty",
                "REAL NOT NULL DEFAULT 0",
            )
            conn.commit()

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        ddl: str,
    ) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _now_pair(self) -> tuple[str, str]:
        now_utc = datetime.now(UTC)
        now_et = now_utc.astimezone(ET)
        return now_utc.isoformat(), now_et.isoformat()

    def log_run_start(
        self,
        *,
        run_id: str,
        trade_date: date,
        signal_path: Path,
        config: dict[str, Any],
        status: str = "started",
        reason: str = "",
    ) -> None:
        ts_utc, ts_et = self._now_pair()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, trade_date, started_at_utc, started_at_et, finished_at_utc,
                    finished_at_et, status, reason, signal_path, config_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    trade_date.isoformat(),
                    ts_utc,
                    ts_et,
                    None,
                    None,
                    status,
                    reason,
                    str(signal_path),
                    json.dumps(config, ensure_ascii=True),
                ),
            )
            conn.commit()

    def update_run_finish(self, run_id: str, *, status: str, reason: str = "") -> None:
        ts_utc, ts_et = self._now_pair()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET finished_at_utc = ?, finished_at_et = ?, status = ?, reason = ?
                WHERE run_id = ?
                """,
                (ts_utc, ts_et, status, reason, run_id),
            )
            conn.commit()

    def log_account_snapshot(
        self,
        *,
        run_id: str,
        snapshot_stage: str,
        account: dict[str, Any],
    ) -> None:
        ts_utc, ts_et = self._now_pair()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO account_snapshots (
                    run_id, snapshot_stage, timestamp_utc, timestamp_et, account_number,
                    status, currency, equity, cash, buying_power
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    snapshot_stage,
                    ts_utc,
                    ts_et,
                    str(account.get("account_number", "")),
                    str(account.get("status", "")),
                    str(account.get("currency", "")),
                    float(account.get("equity", 0.0)),
                    float(account.get("cash", 0.0)),
                    float(account.get("buying_power", 0.0)),
                ),
            )
            conn.commit()

    def log_position_snapshot(
        self,
        *,
        run_id: str,
        snapshot_stage: str,
        positions: pd.DataFrame,
    ) -> None:
        ts_utc, ts_et = self._now_pair()
        if positions.empty:
            return
        with self._connect() as conn:
            rows = []
            for _, row in positions.iterrows():
                rows.append(
                    (
                        run_id,
                        snapshot_stage,
                        ts_utc,
                        ts_et,
                        str(row.get("symbol", "")).upper(),
                        float(row.get("qty", 0.0)),
                        str(row.get("side", "")),
                        float(row.get("market_value", 0.0)),
                        float(row.get("signed_market_value", row.get("market_value", 0.0))),
                        float(row.get("avg_entry_price", 0.0)),
                        float(row.get("unrealized_pl", 0.0)),
                    )
                )
            conn.executemany(
                """
                INSERT INTO position_snapshots (
                    run_id, snapshot_stage, timestamp_utc, timestamp_et, symbol, qty,
                    side, market_value, signed_market_value, avg_entry_price, unrealized_pl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def log_targets(
        self,
        *,
        run_id: str,
        targets: pd.DataFrame,
        target_stage: str,
    ) -> None:
        if targets.empty:
            return
        with self._connect() as conn:
            rows = []
            for _, row in targets.iterrows():
                rows.append(
                    (
                        run_id,
                        target_stage,
                        str(row.get("symbol", "")).upper(),
                        str(row.get("side", "")),
                        str(row.get("sector", "")),
                        float(row.get("score", 0.0)),
                        float(row.get("target_weight", 0.0)),
                        float(row.get("target_notional", 0.0)),
                    )
                )
            conn.executemany(
                """
                INSERT INTO targets (
                    run_id, target_stage, symbol, side, sector, score,
                    target_weight, target_notional
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def log_order_events(self, events: pd.DataFrame) -> None:
        if events.empty:
            return
        with self._connect() as conn:
            rows = []
            for _, row in events.iterrows():
                rows.append(
                    (
                        str(row.get("run_id", "")),
                        int(row.get("pass_num", 1)),
                        str(row.get("event_ts_utc", "")),
                        str(row.get("event_ts_et", "")),
                        str(row.get("symbol", "")).upper(),
                        str(row.get("target_side", "")),
                        str(row.get("order_side", "")),
                        float(row.get("order_notional", 0.0)),
                        float(row.get("order_qty", 0.0)),
                        float(row.get("target_weight", 0.0)),
                        float(row.get("target_notional", 0.0)),
                        float(row.get("current_notional", 0.0)),
                        float(row.get("delta_notional", 0.0)),
                        str(row.get("order_id", "")),
                        str(row.get("status", "")),
                        str(row.get("error", "")),
                    )
                )
            conn.executemany(
                """
                INSERT INTO order_events (
                    run_id, pass_num, event_ts_utc, event_ts_et, symbol, target_side,
                    order_side, order_notional, order_qty, target_weight, target_notional,
                    current_notional, delta_notional, order_id, status, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def upsert_daily_metric(self, record: DailyMetricRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_metrics (
                    trade_date, run_id, equity, prev_equity, daily_return,
                    traded_notional, turnover, pnl_gross, pnl_net, cost_bps,
                    sharpe_20, margin_proxy_bps_20, fitness_proxy_20, max_drawdown_to_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.trade_date,
                    record.run_id,
                    record.equity,
                    record.prev_equity,
                    record.daily_return,
                    record.traded_notional,
                    record.turnover,
                    record.pnl_gross,
                    record.pnl_net,
                    record.cost_bps,
                    record.sharpe_20,
                    record.margin_proxy_bps_20,
                    record.fitness_proxy_20,
                    record.max_drawdown_to_date,
                ),
            )
            conn.commit()

    def get_latest_daily_metric_before(self, trade_date: date) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM daily_metrics
                WHERE trade_date < ?
                ORDER BY trade_date DESC
                LIMIT 1
                """,
                (trade_date.isoformat(),),
            ).fetchone()
        return dict(row) if row else None

    def get_recent_daily_metrics(self, limit: int) -> pd.DataFrame:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM daily_metrics
                ORDER BY trade_date DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame([dict(row) for row in rows])
        return frame.sort_values("trade_date").reset_index(drop=True)

    def get_daily_metrics_between(self, start: date, end: date) -> pd.DataFrame:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM daily_metrics
                WHERE trade_date >= ? AND trade_date <= ?
                ORDER BY trade_date
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(row) for row in rows])

    def get_last_run_trade_date(self, *, exclude_run_id: str = "") -> date | None:
        query = """
            SELECT trade_date
            FROM runs
        """
        params: tuple[object, ...] = ()
        if exclude_run_id:
            query += " WHERE run_id != ?"
            params = (exclude_run_id,)
        query += " ORDER BY trade_date DESC, started_at_utc DESC LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        if not row:
            return None
        return datetime.strptime(str(row["trade_date"]), "%Y-%m-%d").date()

    def log_missed_run(self, trade_date: date, reason: str) -> None:
        ts_utc, ts_et = self._now_pair()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO missed_runs (
                    trade_date, detected_at_utc, detected_at_et, reason
                ) VALUES (?, ?, ?, ?)
                """,
                (trade_date.isoformat(), ts_utc, ts_et, reason),
            )
            conn.commit()

    def count_missed_runs_between(self, start: date, end: date) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(1) AS c
                FROM missed_runs
                WHERE trade_date >= ? AND trade_date <= ?
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchone()
        return int(row["c"]) if row else 0

    def export_daily_csvs(
        self,
        *,
        trade_date: date,
        account: pd.DataFrame,
        positions: pd.DataFrame,
        targets: pd.DataFrame,
        orders: pd.DataFrame,
    ) -> None:
        stamp = trade_date.isoformat()
        account.to_csv(self.logs_dir / f"account_{stamp}.csv", index=False)
        positions.to_csv(self.logs_dir / f"positions_{stamp}.csv", index=False)
        targets.to_csv(self.logs_dir / f"targets_{stamp}.csv", index=False)
        orders.to_csv(self.logs_dir / f"orders_{stamp}.csv", index=False)
