from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import pandas as pd


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from universe_builder import (  # type: ignore  # noqa: E402
    normalize_assets_frame,
    select_liquid_universe,
)


def _sample_assets() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"symbol": "AAA", "tradable": True, "shortable": True, "exchange": "NYSE"},
            {"symbol": "BBB", "tradable": True, "shortable": False, "exchange": "NASDAQ"},
            {"symbol": "CCC", "tradable": True, "shortable": True, "exchange": "NYSE"},
            {"symbol": "EEE", "tradable": True, "shortable": True, "exchange": "NASDAQ"},
            {"symbol": "DDD", "tradable": False, "shortable": True, "exchange": "NYSE"},
        ]
    )


def _sample_bars() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"symbol": "AAA", "t": "2026-02-24T21:00:00Z", "c": 10.0, "v": 1_000},
            {"symbol": "AAA", "t": "2026-02-25T21:00:00Z", "c": 10.0, "v": 1_000},
            {"symbol": "AAA", "t": "2026-02-26T21:00:00Z", "c": 10.0, "v": 1_000},
            {"symbol": "BBB", "t": "2026-02-24T21:00:00Z", "c": 10.0, "v": 2_000},
            {"symbol": "BBB", "t": "2026-02-25T21:00:00Z", "c": 10.0, "v": 2_000},
            {"symbol": "BBB", "t": "2026-02-26T21:00:00Z", "c": 10.0, "v": 2_000},
            {"symbol": "CCC", "t": "2026-02-24T21:00:00Z", "c": 2.0, "v": 10_000},
            {"symbol": "CCC", "t": "2026-02-25T21:00:00Z", "c": 2.0, "v": 10_000},
            {"symbol": "CCC", "t": "2026-02-26T21:00:00Z", "c": 2.0, "v": 10_000},
            {"symbol": "EEE", "t": "2026-02-25T21:00:00Z", "c": 20.0, "v": 1_000},
            {"symbol": "EEE", "t": "2026-02-26T21:00:00Z", "c": 20.0, "v": 1_000},
        ]
    )


def test_normalize_assets_frame_standardizes_symbol_and_bools() -> None:
    raw = pd.DataFrame(
        [
            {"symbol": " aaa ", "tradable": "true", "shortable": "1"},
            {"symbol": "AAA", "tradable": "false", "shortable": "0"},
            {"symbol": "bbb", "tradable": "yes", "shortable": "no"},
        ]
    )
    out = normalize_assets_frame(raw)
    assert out["symbol"].tolist() == ["AAA", "BBB"]
    assert bool(out.iloc[0]["tradable"]) is True
    assert bool(out.iloc[0]["shortable"]) is True
    assert bool(out.iloc[1]["tradable"]) is True
    assert bool(out.iloc[1]["shortable"]) is False


def test_select_liquid_universe_shortable_only_filters_non_shortable() -> None:
    assets = _sample_assets()
    bars = _sample_bars()
    out = select_liquid_universe(
        assets,
        bars,
        asof_date=date(2026, 2, 26),
        lookback_days=3,
        max_symbols=10,
        min_price=3.0,
        min_dollar_volume=5_000.0,
        min_coverage=1.0,
        shortable_only=True,
    )
    assert out["symbol"].tolist() == ["AAA"]


def test_select_liquid_universe_all_tradable_keeps_non_shortable_and_ranks_by_dv() -> None:
    assets = _sample_assets()
    bars = _sample_bars()
    out = select_liquid_universe(
        assets,
        bars,
        asof_date=date(2026, 2, 26),
        lookback_days=3,
        max_symbols=2,
        min_price=3.0,
        min_dollar_volume=5_000.0,
        min_coverage=1.0,
        shortable_only=False,
    )
    assert out["symbol"].tolist() == ["BBB", "AAA"]
