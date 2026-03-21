from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import sys

import pandas as pd
import pytest


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from signal_generator import (  # type: ignore  # noqa: E402
    SignalGenerationError,
    build_signal_frame,
    compute_failed_move_vwap_scores,
    compute_profit_asset_gate_scores,
    compute_profit_asset_gate_proxy_panel,
    compute_profit_asset_gate_proxy_scores,
    load_cached_classifications,
    load_universe_symbols,
    score_profit_asset_gate_proxy_frame,
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


def _profit_asset_gate_bars() -> pd.DataFrame:
    dates = [
        "2026-02-20T21:00:00Z",
        "2026-02-23T21:00:00Z",
        "2026-02-24T21:00:00Z",
        "2026-02-25T21:00:00Z",
        "2026-02-26T21:00:00Z",
        "2026-02-27T21:00:00Z",
    ]
    close_map = {
        "A1": [100, 101, 102, 103, 104, 105],
        "A2": [100, 100, 100, 100, 100, 100],
        "B1": [100, 99, 98, 97, 96, 95],
        "B2": [100, 101, 100, 101, 100, 101],
    }
    rows: list[dict[str, object]] = []
    for symbol, closes in close_map.items():
        for idx, close in enumerate(closes):
            rows.append(
                {
                    "symbol": symbol,
                    "t": dates[idx],
                    "o": close,
                    "h": close + 0.5,
                    "l": close - 0.5,
                    "c": close,
                    "v": 1000 + idx,
                    "vw": close,
                }
            )
    return pd.DataFrame(rows)


def _profit_asset_gate_reference_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    fundamentals = pd.DataFrame(
        [
            {
                "symbol": "A1",
                "effective_date": date(2026, 2, 1),
                "fnd2_ebitdm": 10.0,
                "fnd2_ebitfr": 9.0,
                "fn_assets_fair_val_a": 100.0,
                "is_stale": False,
            },
            {
                "symbol": "A2",
                "effective_date": date(2026, 2, 1),
                "fnd2_ebitdm": 4.0,
                "fnd2_ebitfr": 3.0,
                "fn_assets_fair_val_a": 50.0,
                "is_stale": False,
            },
            {
                "symbol": "B1",
                "effective_date": date(2026, 2, 1),
                "fnd2_ebitdm": 6.0,
                "fnd2_ebitfr": 7.0,
                "fn_assets_fair_val_a": 30.0,
                "is_stale": False,
            },
            {
                "symbol": "B2",
                "effective_date": date(2026, 2, 1),
                "fnd2_ebitdm": 1.0,
                "fnd2_ebitfr": 2.0,
                "fn_assets_fair_val_a": 10.0,
                "is_stale": False,
            },
        ]
    )
    classifications = pd.DataFrame(
        [
            {
                "symbol": "A1",
                "effective_date": date(2026, 2, 1),
                "sector": "Tech",
                "industry": "Software",
            },
            {
                "symbol": "A2",
                "effective_date": date(2026, 2, 1),
                "sector": "Tech",
                "industry": "Software",
            },
            {
                "symbol": "B1",
                "effective_date": date(2026, 2, 1),
                "sector": "Health",
                "industry": "Biotech",
            },
            {
                "symbol": "B2",
                "effective_date": date(2026, 2, 1),
                "sector": "Health",
                "industry": "Biotech",
            },
        ]
    )
    return fundamentals, classifications


def _profit_asset_gate_proxy_bars() -> pd.DataFrame:
    dates = [
        "2026-02-23T21:00:00Z",
        "2026-02-24T21:00:00Z",
        "2026-02-25T21:00:00Z",
        "2026-02-26T21:00:00Z",
        "2026-02-27T21:00:00Z",
    ]
    close_map = {
        "P1": [100.0, 101.0, 102.0, 103.0, 104.0],
        "P2": [100.0, 103.0, 100.0, 104.0, 110.0],
        "P3": [100.0, 100.0, 100.2, 100.1, 100.1],
        "P4": [100.0, 95.0, 100.0, 90.0, 88.0],
    }
    rows: list[dict[str, object]] = []
    for symbol, closes in close_map.items():
        for idx, close in enumerate(closes):
            rows.append(
                {
                    "symbol": symbol,
                    "t": dates[idx],
                    "o": close,
                    "h": close + 0.5,
                    "l": close - 0.5,
                    "c": close,
                    "v": 1000 + idx,
                    "vw": close,
                }
            )
    return pd.DataFrame(rows)


def test_compute_profit_asset_gate_scores_matches_expected_formula() -> None:
    bars = _profit_asset_gate_bars()
    fundamentals, classifications = _profit_asset_gate_reference_data()

    scores, diagnostics = compute_profit_asset_gate_scores(
        ["A1", "A2", "B1", "B2"],
        bars,
        fundamentals=fundamentals,
        classifications=classifications,
        asof_date=date(2026, 2, 27),
        min_scored_symbols=2,
    )

    score_map = dict(zip(scores["symbol"], scores["score"]))
    assert score_map["A1"] == pytest.approx(1.0, abs=1e-9)
    assert score_map["B1"] == pytest.approx(1.5, abs=1e-9)
    assert score_map["A2"] == pytest.approx(0.0, abs=1e-9)
    assert score_map["B2"] == pytest.approx(0.0, abs=1e-9)

    diag = diagnostics.set_index("symbol")
    assert bool(diag.loc["A1", "gate_passed"]) is True
    assert bool(diag.loc["A2", "gate_passed"]) is False
    assert diag.loc["B1", "mom_rank"] == pytest.approx(0.5, abs=1e-9)


def test_compute_profit_asset_gate_scores_zeroes_missing_or_stale_inputs() -> None:
    bars = _profit_asset_gate_bars()
    fundamentals, classifications = _profit_asset_gate_reference_data()
    fundamentals.loc[fundamentals["symbol"] == "A1", "is_stale"] = True
    classifications.loc[classifications["symbol"] == "B1", "industry"] = pd.NA

    scores, diagnostics = compute_profit_asset_gate_scores(
        ["A1", "A2", "B1", "B2"],
        bars,
        fundamentals=fundamentals,
        classifications=classifications,
        asof_date=date(2026, 2, 27),
        min_scored_symbols=1,
    )

    score_map = dict(zip(scores["symbol"], scores["score"]))
    diag = diagnostics.set_index("symbol")
    assert score_map["A1"] == pytest.approx(0.0, abs=1e-9)
    assert score_map["B1"] == pytest.approx(0.0, abs=1e-9)
    assert diag.loc["A1", "missing_reason"] == "stale_fundamental"
    assert diag.loc["B1", "missing_reason"] == "missing_industry"


def test_compute_profit_asset_gate_proxy_scores_uses_price_only_group_ranks() -> None:
    scores, diagnostics = compute_profit_asset_gate_proxy_scores(
        _profit_asset_gate_proxy_bars(),
        sector_map={"P1": "Tech", "P2": "Tech", "P3": "Tech", "P4": "Tech"},
        profit_window=3,
        asset_window=3,
        mom_window=2,
        min_scored_symbols=3,
    )

    score_map = dict(zip(scores["symbol"], scores["score"]))
    diag = diagnostics.set_index("symbol")

    assert bool(diag.loc["P1", "gate_passed"]) is True
    assert bool(diag.loc["P2", "gate_passed"]) is False
    assert bool(diag.loc["P3", "gate_passed"]) is True
    assert bool(diag.loc["P4", "gate_passed"]) is False
    assert diag["missing_reason"].eq("").all()
    assert score_map["P1"] > 0.0
    assert score_map["P2"] == pytest.approx(0.0, abs=1e-9)
    assert score_map["P3"] == pytest.approx(0.0, abs=1e-9)
    assert score_map["P4"] == pytest.approx(0.0, abs=1e-9)


def test_proxy_panel_latest_day_matches_latest_score_function() -> None:
    panel = compute_profit_asset_gate_proxy_panel(
        _profit_asset_gate_proxy_bars(),
        sector_map={"P1": "Tech", "P2": "Tech", "P3": "Tech", "P4": "Tech"},
        group_level="sector",
        profit_window=3,
        asset_window=3,
        mom_window=2,
    )
    latest_date = panel["trade_date"].max()
    latest = panel[panel["trade_date"] == latest_date].copy()
    direct_scores, _ = score_profit_asset_gate_proxy_frame(latest, min_scored_symbols=3)
    wrapper_scores, _ = compute_profit_asset_gate_proxy_scores(
        _profit_asset_gate_proxy_bars(),
        sector_map={"P1": "Tech", "P2": "Tech", "P3": "Tech", "P4": "Tech"},
        group_level="sector",
        profit_window=3,
        asset_window=3,
        mom_window=2,
        min_scored_symbols=3,
    )

    direct_map = dict(zip(direct_scores["symbol"], direct_scores["score"]))
    wrapper_map = dict(zip(wrapper_scores["symbol"], wrapper_scores["score"]))
    assert direct_map == wrapper_map


def test_load_cached_classifications_required_raises_without_bootstrap(tmp_path: Path) -> None:
    with pytest.raises(SignalGenerationError):
        load_cached_classifications(tmp_path, required=True)
