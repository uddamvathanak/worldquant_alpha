from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import pandas as pd


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from backtest_engine import SplitWindows  # type: ignore  # noqa: E402
from research_materialized_cache import ResearchMaterializedCache  # type: ignore  # noqa: E402


def test_materialized_cache_round_trips_prepared_inputs(tmp_path: Path) -> None:
    cache = ResearchMaterializedCache(tmp_path)
    snapshot_path = tmp_path / "classifications.csv"
    snapshot_path.write_text("symbol\nAAA\n", encoding="utf-8")
    prepared_key = cache.build_prepared_key(
        end_date=date(2026, 3, 20),
        classification_snapshot_path=snapshot_path,
        classification_snapshot_date=date(2026, 3, 17),
        feed="sip",
        train_days=756,
        oos_days=252,
        test_days=252,
        min_universe=2500,
        min_universe_ratio=0.9,
    )
    bars = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA"],
            "trade_date": [date(2026, 1, 6), date(2026, 1, 7)],
            "o": [10.0, 10.5],
            "h": [10.5, 11.0],
            "l": [9.5, 10.0],
            "c": [10.2, 10.7],
            "v": [1000, 1100],
            "vw": [10.1, 10.6],
            "n": [1, 1],
        }
    )
    open_returns = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA"],
            "trade_date": [date(2026, 1, 6), date(2026, 1, 7)],
            "period_return": [0.01, 0.02],
        }
    )
    execution_map = pd.DataFrame(
        {
            "execution_date": [date(2026, 1, 7)],
            "signal_date": [date(2026, 1, 6)],
            "next_execution_date": [date(2026, 1, 8)],
        }
    )
    prepared = {
        "prepared_cache_key": prepared_key,
        "end_date": "2026-03-20",
        "feed": "sip",
        "min_universe": 2500,
        "min_universe_ratio": 0.9,
        "classification_snapshot_path": snapshot_path,
        "classification_snapshot_date": "2026-03-17",
        "splits": SplitWindows(
            latest_completed_date=date(2026, 3, 20),
            usable_end_date=date(2026, 3, 19),
            train_dates=[date(2026, 1, 1)],
            oos_dates=[date(2026, 2, 1)],
            test_dates=[date(2026, 3, 1)],
        ),
        "bars": bars,
        "open_returns": open_returns,
        "execution_map": execution_map,
        "universe_lookup": {date(2026, 1, 6): pd.DataFrame({"symbol": ["AAA"], "avg_close": [10.2]})},
        "coverage_ratio": 1.0,
        "degraded_depth": False,
    }

    cache.save_prepared_inputs(prepared)
    loaded = cache.load_prepared_inputs(prepared_key)

    assert loaded is not None
    assert loaded["prepared_cache_key"] == prepared_key
    assert loaded["bars"].equals(bars)
    assert loaded["open_returns"].equals(open_returns)
    assert loaded["execution_map"].equals(execution_map)
    assert date(2026, 1, 6) in loaded["universe_lookup"]
