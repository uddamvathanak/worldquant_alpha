from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import sys

import pandas as pd


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from signal_generator import (  # type: ignore  # noqa: E402
    build_signal_frame,
    compute_failed_move_vwap_scores,
    load_universe_symbols,
)


def test_compute_failed_move_vwap_scores_returns_latest_scores() -> None:
    frame = pd.DataFrame(
        [
            {"symbol": "AAA", "t": "2026-02-24T21:00:00Z", "o": 10, "h": 10.5, "l": 9.8, "c": 10.2, "v": 1000, "vw": 10.1},
            {"symbol": "BBB", "t": "2026-02-24T21:00:00Z", "o": 20, "h": 20.5, "l": 19.8, "c": 20.4, "v": 1500, "vw": 20.2},
            {"symbol": "CCC", "t": "2026-02-24T21:00:00Z", "o": 30, "h": 30.8, "l": 29.9, "c": 30.1, "v": 1800, "vw": 30.2},
            {"symbol": "AAA", "t": "2026-02-25T21:00:00Z", "o": 10.2, "h": 10.7, "l": 10.0, "c": 10.6, "v": 1200, "vw": 10.4},
            {"symbol": "BBB", "t": "2026-02-25T21:00:00Z", "o": 20.4, "h": 20.6, "l": 20.0, "c": 20.1, "v": 1100, "vw": 20.3},
            {"symbol": "CCC", "t": "2026-02-25T21:00:00Z", "o": 30.1, "h": 30.5, "l": 29.7, "c": 29.9, "v": 1700, "vw": 30.0},
            {"symbol": "AAA", "t": "2026-02-26T21:00:00Z", "o": 10.6, "h": 10.8, "l": 10.2, "c": 10.3, "v": 1300, "vw": 10.5},
            {"symbol": "BBB", "t": "2026-02-26T21:00:00Z", "o": 20.1, "h": 20.7, "l": 19.9, "c": 20.5, "v": 1400, "vw": 20.4},
            {"symbol": "CCC", "t": "2026-02-26T21:00:00Z", "o": 29.9, "h": 30.2, "l": 29.5, "c": 29.6, "v": 1600, "vw": 29.8},
        ]
    )
    out = compute_failed_move_vwap_scores(frame, smoothing=2)
    assert not out.empty
    assert set(out.columns) == {"symbol", "score"}
    assert out["score"].notna().all()
    assert set(out["symbol"]) == {"AAA", "BBB", "CCC"}


def test_build_signal_frame_applies_default_sector() -> None:
    scores = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "score": [0.7, -0.2],
        }
    )
    out = build_signal_frame(
        scores,
        trade_date=date(2026, 3, 1),
        sector_map={"AAA": "Tech"},
        default_sector="ALL",
    )
    aaa = out[out["symbol"] == "AAA"].iloc[0]
    bbb = out[out["symbol"] == "BBB"].iloc[0]
    assert aaa["sector"] == "Tech"
    assert bbb["sector"] == "ALL"
    assert (out["asof_date"] == "2026-03-01").all()


def test_load_universe_symbols_falls_back_to_latest_signal_file(tmp_path: Path) -> None:
    signals_dir = tmp_path / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)

    older = signals_dir / "2026-02-27.csv"
    newer = signals_dir / "2026-02-28.csv"
    pd.DataFrame({"symbol": ["AAA", "BBB"], "score": [1.0, -1.0], "sector": ["ALL", "ALL"]}).to_csv(
        older, index=False
    )
    pd.DataFrame({"symbol": ["CCC", "DDD"], "score": [1.0, -1.0], "sector": ["ALL", "ALL"]}).to_csv(
        newer, index=False
    )

    older_ts = 1_700_000_000
    newer_ts = 1_700_100_000
    os.utime(older, (older_ts, older_ts))
    os.utime(newer, (newer_ts, newer_ts))

    universe_file = tmp_path / "private" / "universe.csv"  # intentionally missing
    symbols = load_universe_symbols(universe_file=universe_file, signals_dir=signals_dir)
    assert symbols == ["CCC", "DDD"]
