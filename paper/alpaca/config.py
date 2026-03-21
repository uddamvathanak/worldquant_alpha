from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from alpha_registry import MODEL_RESEARCH_SELECTED, registry_model_names
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


def _read_choice(name: str, default: str, allowed: set[str]) -> str:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw not in allowed:
        return default
    return raw


@dataclass(slots=True)
class RunConfig:
    base_dir: Path
    env_path: Path
    logs_dir: Path
    state_dir: Path
    signals_dir: Path
    tmp_dir: Path
    private_dir: Path
    cache_dir: Path
    backtests_dir: Path
    research_runs_dir: Path
    search_runs_dir: Path
    reference_dir: Path
    classifications_dir: Path
    classifications_latest_file: Path
    symbol_master_file: Path
    fundamentals_file: Path
    classifications_file: Path
    selected_strategy_file: Path
    shadow_strategy_file: Path
    research_cache_db_path: Path
    db_path: Path
    classification_source: str
    signal_model: str
    top_n: int
    gross_exposure: float
    book_mode: str
    kill_switch_daily_return: float
    round_trip_cost_bps: float
    min_order_notional: float
    bp_utilization: float
    margin_buffer_notional: float
    max_retry_passes: int
    scheduler_task_name: str
    scheduler_time_et: str
    alpha_search_batch_size: int
    alpha_search_max_runtime_min: int
    alpha_search_task_name: str

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
            self.cache_dir,
            self.backtests_dir,
            self.research_runs_dir,
            self.search_runs_dir,
            self.reference_dir,
            self.classifications_dir,
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
    private_dir = root / "private"
    cache_dir = private_dir / "cache"
    backtests_dir = private_dir / "backtests"
    research_runs_dir = private_dir / "research_runs"
    search_runs_dir = private_dir / "search_runs"
    reference_dir = root / "private" / "reference"
    classifications_dir = reference_dir / "classifications"
    book_mode = (os.getenv("ALPACA_BOOK_MODE", "sector").strip().lower() or "sector")
    if book_mode not in {"sector", "none"}:
        book_mode = "sector"
    classification_source = _read_choice(
        "ALPACA_CLASSIFICATION_SOURCE",
        "fmp",
        {"fmp"},
    )
    allowed_models = {
        "failed_move_vwap",
        "profit_asset_gate",
        "profit_asset_gate_proxy",
        MODEL_RESEARCH_SELECTED,
        *registry_model_names(),
    }
    raw_signal_model = os.getenv("ALPACA_SIGNAL_MODEL", "").strip().lower()
    signal_model = raw_signal_model if raw_signal_model in allowed_models else "profit_asset_gate_proxy"

    cfg = RunConfig(
        base_dir=root,
        env_path=env_path,
        logs_dir=root / "logs",
        state_dir=root / "state",
        signals_dir=root / "signals",
        tmp_dir=root / "tmp",
        private_dir=private_dir,
        cache_dir=cache_dir,
        backtests_dir=backtests_dir,
        research_runs_dir=research_runs_dir,
        search_runs_dir=search_runs_dir,
        reference_dir=reference_dir,
        classifications_dir=classifications_dir,
        classifications_latest_file=reference_dir / "classifications_latest.csv",
        symbol_master_file=reference_dir / "symbol_master.csv",
        fundamentals_file=reference_dir / "fundamentals.csv",
        classifications_file=reference_dir / "classifications.csv",
        selected_strategy_file=private_dir / "selected_strategy.json",
        shadow_strategy_file=private_dir / "shadow_strategy.json",
        research_cache_db_path=root / "state" / "research_cache.db",
        db_path=root / "state" / "paper_trading.db",
        classification_source=classification_source,
        signal_model=signal_model,
        top_n=_read_int("ALPACA_TOP_N", 30),
        gross_exposure=_read_float("ALPACA_GROSS_EXPOSURE", 4.0),
        book_mode=book_mode,
        kill_switch_daily_return=_read_float("ALPACA_KILL_SWITCH_DAILY_RETURN", -0.02),
        round_trip_cost_bps=_read_float("ALPACA_ROUND_TRIP_COST_BPS", 5.0),
        min_order_notional=_read_float("ALPACA_MIN_ORDER_NOTIONAL", 50.0),
        bp_utilization=_read_float("ALPACA_BP_UTILIZATION", 0.90),
        margin_buffer_notional=_read_float("ALPACA_MARGIN_BUFFER_NOTIONAL", 0.0),
        max_retry_passes=_read_int("ALPACA_MAX_RETRY_PASSES", 3),
        scheduler_task_name=os.getenv(
            "ALPACA_SCHEDULER_TASK_NAME",
            "WQA_Alpaca_Rebalance_0935ET",
        ).strip()
        or "WQA_Alpaca_Rebalance_0935ET",
        scheduler_time_et=os.getenv("ALPACA_SCHEDULER_TIME_ET", "09:35").strip()
        or "09:35",
        alpha_search_batch_size=_read_int("ALPHA_SEARCH_BATCH_SIZE", 10),
        alpha_search_max_runtime_min=_read_int("ALPHA_SEARCH_MAX_RUNTIME_MIN", 480),
        alpha_search_task_name=os.getenv(
            "ALPHA_SEARCH_TASK_NAME",
            "WQA_Alpaca_Research_2300",
        ).strip()
        or "WQA_Alpaca_Research_2300",
    )
    cfg.ensure_runtime_dirs()
    return cfg
