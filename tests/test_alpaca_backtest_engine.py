from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import pandas as pd
import pytest


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from backtest_engine import (  # type: ignore  # noqa: E402
    BacktestCandidate,
    _select_best_candidate,
    build_split_windows,
    canonicalize_bars,
    run_backtest_candidate,
)


def _backtest_fixture_bars() -> pd.DataFrame:
    dates = [
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 1, 9),
    ]
    close_map = {
        "AAA": [100, 101, 102, 103, 104, 105],
        "BBB": [100, 99, 98, 97, 96, 95],
        "CCC": [100, 100.5, 101, 101.5, 102, 102.5],
        "DDD": [100, 99.5, 99.0, 98.5, 98.0, 97.5],
    }
    rows: list[dict[str, object]] = []
    for symbol, closes in close_map.items():
        for idx, trade_date in enumerate(dates):
            close = closes[idx]
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "o": close,
                    "h": close + 0.5,
                    "l": close - 0.5,
                    "c": close,
                    "v": 1000,
                    "vw": close,
                    "n": 1,
                }
            )
    return pd.DataFrame(rows)


def _backtest_classifications() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "canonical_symbol": "AAA",
                "snapshot_date": "2026-03-17",
                "sector": "Tech",
                "industry": "Software",
                "is_delisted": False,
                "delisted_date": "",
                "original_symbol": "AAA",
                "source": "fmp",
            },
            {
                "symbol": "BBB",
                "canonical_symbol": "BBB",
                "snapshot_date": "2026-03-17",
                "sector": "Tech",
                "industry": "Software",
                "is_delisted": False,
                "delisted_date": "",
                "original_symbol": "BBB",
                "source": "fmp",
            },
            {
                "symbol": "CCC",
                "canonical_symbol": "CCC",
                "snapshot_date": "2026-03-17",
                "sector": "Health",
                "industry": "Biotech",
                "is_delisted": False,
                "delisted_date": "",
                "original_symbol": "CCC",
                "source": "fmp",
            },
            {
                "symbol": "DDD",
                "canonical_symbol": "DDD",
                "snapshot_date": "2026-03-17",
                "sector": "Health",
                "industry": "Biotech",
                "is_delisted": False,
                "delisted_date": "",
                "original_symbol": "DDD",
                "source": "fmp",
            },
        ]
    )


def test_build_split_windows_uses_second_last_day_as_usable_end() -> None:
    trading_days = [day.date() for day in pd.bdate_range("2020-01-01", periods=1700)]
    splits = build_split_windows(
        trading_days,
        end_date=trading_days[-1],
        train_days=1008,
        oos_days=252,
        test_days=252,
    )

    assert len(splits.train_dates) == 1008
    assert len(splits.oos_dates) == 252
    assert len(splits.test_dates) == 252
    assert splits.latest_completed_date == trading_days[-1]
    assert splits.usable_end_date == trading_days[-2]


def test_select_best_candidate_uses_turnover_and_window_tiebreaks() -> None:
    leaderboard = pd.DataFrame(
        [
            {
                "profit_window": 63,
                "asset_window": 63,
                "mom_window": 5,
                "group_level": "sector",
                "book_mode": "sector",
                "top_n": 30,
                "gross_exposure": 4.0,
                "fitness_proxy": 2.0,
                "sharpe_proxy": 1.5,
                "turnover_mean": 0.40,
                "window_sum": 131,
            },
            {
                "profit_window": 42,
                "asset_window": 42,
                "mom_window": 5,
                "group_level": "sector",
                "book_mode": "sector",
                "top_n": 30,
                "gross_exposure": 4.0,
                "fitness_proxy": 2.0,
                "sharpe_proxy": 1.5,
                "turnover_mean": 0.20,
                "window_sum": 89,
            },
        ]
    )
    best = _select_best_candidate(leaderboard)
    assert best.profit_window == 42
    assert best.asset_window == 42


def test_run_backtest_candidate_replays_delay_one_returns() -> None:
    bars = _backtest_fixture_bars()
    classifications = _backtest_classifications()
    universe_lookup = {
        date(2026, 1, 6): pd.DataFrame({"symbol": ["AAA", "BBB", "CCC", "DDD"]}),
        date(2026, 1, 7): pd.DataFrame({"symbol": ["AAA", "BBB", "CCC", "DDD"]}),
    }
    execution_map = pd.DataFrame(
        [
            {
                "execution_date": date(2026, 1, 7),
                "signal_date": date(2026, 1, 6),
                "next_execution_date": date(2026, 1, 8),
            },
            {
                "execution_date": date(2026, 1, 8),
                "signal_date": date(2026, 1, 7),
                "next_execution_date": date(2026, 1, 9),
            },
        ]
    )
    candidate = BacktestCandidate(
        profit_window=2,
        asset_window=2,
        mom_window=2,
        group_level="sector",
        book_mode="sector",
        top_n=2,
        gross_exposure=2.0,
    )

    daily, targets, positions = run_backtest_candidate(
        bars,
        classifications,
        universe_lookup,
        execution_map,
        candidate,
        round_trip_cost_bps=5.0,
        initial_equity=100_000.0,
        min_scored_symbols=2,
    )

    assert len(daily) == 2
    assert daily.iloc[0]["execution_date"] == "2026-01-07"
    assert not targets.empty
    assert not positions.empty
    assert daily["equity"].iloc[-1] != pytest.approx(100_000.0, abs=1e-9)


def test_canonicalize_bars_merges_symbol_aliases() -> None:
    bars = pd.DataFrame(
        [
            {
                "symbol": "OLD",
                "trade_date": date(2026, 1, 5),
                "o": 10.0,
                "h": 10.5,
                "l": 9.5,
                "c": 10.2,
                "v": 100,
                "vw": 10.1,
                "n": 1,
            },
            {
                "symbol": "NEW",
                "trade_date": date(2026, 1, 6),
                "o": 10.2,
                "h": 10.7,
                "l": 10.0,
                "c": 10.6,
                "v": 120,
                "vw": 10.4,
                "n": 1,
            },
        ]
    )
    symbol_master = pd.DataFrame(
        [
            {"symbol": "OLD", "canonical_symbol": "NEW", "original_symbol": "OLD"},
            {"symbol": "NEW", "canonical_symbol": "NEW", "original_symbol": "NEW"},
        ]
    )

    out = canonicalize_bars(bars, symbol_master)
    assert set(out["symbol"]) == {"NEW"}
    assert len(out) == 2
