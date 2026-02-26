from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def et_now() -> datetime:
    return datetime.now(ET)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def et_now_iso() -> str:
    return et_now().isoformat()


def parse_trade_date(value: str | None) -> date:
    if not value:
        return et_now().date()
    return datetime.strptime(value, "%Y-%m-%d").date()


def _read_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _read_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return float(raw)


@dataclass(slots=True)
class RunConfig:
    base_dir: Path
    env_path: Path
    logs_dir: Path
    state_dir: Path
    signals_dir: Path
    tmp_dir: Path
    private_dir: Path
    db_path: Path
    top_n: int
    gross_exposure: float
    kill_switch_daily_return: float
    round_trip_cost_bps: float
    min_order_notional: float
    scheduler_task_name: str
    scheduler_time_et: str

    @property
    def long_gross_target(self) -> float:
        return self.gross_exposure / 2.0

    @property
    def short_gross_target(self) -> float:
        return self.gross_exposure / 2.0

    @property
    def round_trip_cost_rate(self) -> float:
        return self.round_trip_cost_bps / 10_000.0

    def ensure_runtime_dirs(self) -> None:
        for path in [
            self.logs_dir,
            self.state_dir,
            self.signals_dir,
            self.tmp_dir,
            self.private_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)

    def require_alpaca_credentials(self) -> tuple[str, str]:
        key = os.getenv("APCA_API_KEY_ID", "").strip()
        secret = os.getenv("APCA_API_SECRET_KEY", "").strip()
        if not key or not secret:
            raise RuntimeError(
                f"Missing APCA_API_KEY_ID/APCA_API_SECRET_KEY in {self.env_path}."
            )
        return key, secret

    def to_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key, value in list(payload.items()):
            if isinstance(value, Path):
                payload[key] = str(value)
        return payload


def load_config(base_dir: Path | None = None) -> RunConfig:
    root = (base_dir or Path(__file__).resolve().parent).resolve()
    env_path = root / ".env"
    load_dotenv(dotenv_path=env_path)

    cfg = RunConfig(
        base_dir=root,
        env_path=env_path,
        logs_dir=root / "logs",
        state_dir=root / "state",
        signals_dir=root / "signals",
        tmp_dir=root / "tmp",
        private_dir=root / "private",
        db_path=root / "state" / "paper_trading.db",
        top_n=_read_int("ALPACA_TOP_N", 30),
        gross_exposure=_read_float("ALPACA_GROSS_EXPOSURE", 0.80),
        kill_switch_daily_return=_read_float("ALPACA_KILL_SWITCH_DAILY_RETURN", -0.02),
        round_trip_cost_bps=_read_float("ALPACA_ROUND_TRIP_COST_BPS", 5.0),
        min_order_notional=_read_float("ALPACA_MIN_ORDER_NOTIONAL", 50.0),
        scheduler_task_name=os.getenv(
            "ALPACA_SCHEDULER_TASK_NAME",
            "WQA_Alpaca_Rebalance_0935ET",
        ).strip()
        or "WQA_Alpaca_Rebalance_0935ET",
        scheduler_time_et=os.getenv("ALPACA_SCHEDULER_TIME_ET", "09:35").strip()
        or "09:35",
    )
    cfg.ensure_runtime_dirs()
    return cfg

