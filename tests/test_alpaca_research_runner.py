from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from research_runner import (  # type: ignore  # noqa: E402
    _apply_sector_vs_none_rule,
    _filter_oos_survivors,
    _filter_unseen_passers,
    _prepare_inputs,
    build_parser,
)


def _candidate_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "candidate_name": "sector_candidate",
                "alpha_name": "rev_close_1d",
                "family": "short_reversion",
                "params": {},
                "group_level": "sector",
                "book_mode": "sector",
                "top_n": 30,
                "gross_exposure": 4.0,
                "signal_decay": 0,
                "score_truncation": None,
                "oos_returns": 0.10,
                "oos_fitness_proxy": 0.20,
                "oos_sharpe_proxy": 0.60,
                "oos_max_drawdown": 0.10,
                "oos_days_with_full_book_ratio": 0.95,
                "test_returns": 0.12,
                "test_fitness_proxy": 0.30,
                "test_sharpe_proxy": 0.90,
                "test_max_drawdown": 0.14,
                "test_turnover_mean": 2.0,
                "test_positive_month_ratio": 0.75,
                "test_sector_concentration_max": 0.30,
            },
            {
                "candidate_name": "none_candidate",
                "alpha_name": "smooth_momentum",
                "family": "momentum",
                "params": {"window": 20},
                "group_level": "market",
                "book_mode": "none",
                "top_n": 30,
                "gross_exposure": 4.0,
                "signal_decay": 0,
                "score_truncation": None,
                "oos_returns": 0.11,
                "oos_fitness_proxy": 0.25,
                "oos_sharpe_proxy": 0.70,
                "oos_max_drawdown": 0.11,
                "oos_days_with_full_book_ratio": 0.95,
                "test_returns": 0.13,
                "test_fitness_proxy": 0.32,
                "test_sharpe_proxy": 0.95,
                "test_max_drawdown": 0.16,
                "test_turnover_mean": 2.2,
                "test_positive_month_ratio": 0.80,
                "test_sector_concentration_max": 0.60,
            },
        ]
    )


def test_research_runner_filters_oos_and_unseen_candidates() -> None:
    frame = _candidate_frame()
    oos = _filter_oos_survivors(frame)
    unseen = _filter_unseen_passers(oos)
    assert set(oos["candidate_name"]) == {"sector_candidate", "none_candidate"}
    assert set(unseen["candidate_name"]) == {"sector_candidate", "none_candidate"}


def test_research_runner_sector_rule_rejects_overconcentrated_none_book() -> None:
    frame = _candidate_frame()
    filtered = _apply_sector_vs_none_rule(frame)
    assert filtered["book_mode"].tolist() == ["sector"]


def test_research_runner_parser_exposes_grid_controls() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--alpha-set",
            "wave1",
            "--group-level-grid",
            "market,sector",
            "--book-mode-grid",
            "sector,none",
            "--top-n-grid",
            "30,50",
            "--decay-grid",
            "0,3",
            "--truncation-grid",
            "none,0.05",
        ]
    )
    assert args.alpha_set == "wave1"
    assert args.group_level_grid == "market,sector"


class _BrokerFixture:
    def __init__(self, bars: pd.DataFrame, trading_days: list[date]) -> None:
        self._bars = bars
        self._trading_days = trading_days

    def list_trading_days(self, start, end):  # type: ignore[no-untyped-def]
        return [day for day in self._trading_days if start <= day <= end]

    def get_daily_bars(self, symbols, *, start, end, timeframe, adjustment, limit, feed):  # type: ignore[no-untyped-def]
        return self._bars[
            self._bars["symbol"].isin([str(symbol).upper() for symbol in symbols])
            & (self._bars["trade_date"] >= start)
            & (self._bars["trade_date"] <= end)
        ].copy()


def test_prepare_inputs_uses_materialized_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reference_dir = tmp_path / "reference"
    class_dir = reference_dir / "classifications"
    class_dir.mkdir(parents=True, exist_ok=True)
    snapshot = class_dir / "2026-03-17.csv"
    pd.DataFrame(
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
                "industry": "Hardware",
                "is_delisted": False,
                "delisted_date": "",
                "original_symbol": "BBB",
                "source": "fmp",
            },
        ]
    ).to_csv(snapshot, index=False)
    pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "canonical_symbol": "AAA",
                "original_symbol": "AAA",
                "first_seen_date": "2026-01-01",
                "last_seen_date": "2026-03-17",
                "is_delisted": False,
                "delisted_date": "",
                "source": "fmp",
            },
            {
                "symbol": "BBB",
                "canonical_symbol": "BBB",
                "original_symbol": "BBB",
                "first_seen_date": "2026-01-01",
                "last_seen_date": "2026-03-17",
                "is_delisted": False,
                "delisted_date": "",
                "source": "fmp",
            },
        ]
    ).to_csv(reference_dir / "symbol_master.csv", index=False)

    trading_days = [day.date() for day in pd.bdate_range("2026-01-01", periods=8)]
    bars = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "o": price,
                "h": price + 0.5,
                "l": price - 0.5,
                "c": price + 0.1,
                "v": 1000,
                "vw": price + 0.05,
                "n": 1,
            }
            for symbol, base in [("AAA", 10.0), ("BBB", 20.0)]
            for price, trade_date in zip([base + idx for idx in range(len(trading_days))], trading_days)
        ]
    )
    broker = _BrokerFixture(bars, trading_days)
    cfg = SimpleNamespace(reference_dir=reference_dir, cache_dir=tmp_path / "cache")
    call_count = {"build_universe_lookup": 0}
    def wrapped_build_universe_lookup(*args, **kwargs):  # type: ignore[no-untyped-def]
        call_count["build_universe_lookup"] += 1
        signal_dates = list(kwargs.get("signal_dates", []))
        return {
            signal_date: pd.DataFrame({"symbol": ["AAA", "BBB"], "avg_close": [10.0, 20.0]})
            for signal_date in signal_dates
        }

    monkeypatch.setattr(sys.modules["research_runner"], "build_universe_lookup", wrapped_build_universe_lookup)

    _prepare_inputs(
        cfg=cfg,
        broker=broker,
        end_date=trading_days[-1],
        classification_snapshot_date=date(2026, 3, 17),
        feed="sip",
        train_days=4,
        oos_days=1,
        test_days=1,
        min_universe=1,
        min_universe_ratio=0.5,
    )
    _prepare_inputs(
        cfg=cfg,
        broker=broker,
        end_date=trading_days[-1],
        classification_snapshot_date=date(2026, 3, 17),
        feed="sip",
        train_days=4,
        oos_days=1,
        test_days=1,
        min_universe=1,
        min_universe_ratio=0.5,
    )

    assert call_count["build_universe_lookup"] == 1
