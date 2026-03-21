from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import pytest


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from alpha_templates import (  # type: ignore  # noqa: E402
    apply_signal_decay,
    compute_alpha_panel,
    compute_alpha_score_panel,
    score_alpha_frame,
)


def _bars_fixture() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=260)
    close_map = {
        "AAA": [100 + 0.5 * idx for idx in range(len(dates))],
        "BBB": [100 - 0.4 * idx for idx in range(len(dates))],
        "CCC": [100 + 0.1 * idx for idx in range(len(dates))],
    }
    rows: list[dict[str, object]] = []
    for symbol, closes in close_map.items():
        for idx, trade_date in enumerate(dates):
            close = closes[idx]
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date.date(),
                    "o": close,
                    "h": close + 0.5,
                    "l": close - 0.5,
                    "c": close,
                    "v": 1_000 + idx,
                    "vw": close,
                    "n": 1,
                }
            )
    return pd.DataFrame(rows)


def _classifications_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "canonical_symbol": "AAA",
                "snapshot_date": "2026-03-19",
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
                "snapshot_date": "2026-03-19",
                "sector": "Tech",
                "industry": "Hardware",
                "is_delisted": False,
                "delisted_date": "",
                "original_symbol": "BBB",
                "source": "fmp",
            },
            {
                "symbol": "CCC",
                "canonical_symbol": "CCC",
                "snapshot_date": "2026-03-19",
                "sector": "Health",
                "industry": "Biotech",
                "is_delisted": False,
                "delisted_date": "",
                "original_symbol": "CCC",
                "source": "fmp",
            },
        ]
    )


def test_compute_alpha_panel_supports_market_group_level() -> None:
    panel = compute_alpha_panel(
        "rev_close_1d",
        _bars_fixture(),
        classifications=_classifications_fixture(),
        group_level="market",
    )
    assert not panel.empty
    assert set(panel["group_key"]) == {"MARKET"}


def test_score_alpha_frame_truncation_caps_absolute_contribution() -> None:
    latest = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB", "CCC"],
            "sector": ["Tech", "Tech", "Health"],
            "industry": ["Software", "Hardware", "Biotech"],
            "canonical_symbol": ["AAA", "BBB", "CCC"],
            "group_key": ["MARKET", "MARKET", "MARKET"],
            "alpha_raw": [3.0, 2.0, 1.0],
        }
    )
    scores, diagnostics = score_alpha_frame(
        "rev_close_1d",
        latest,
        min_scored_symbols=3,
        score_truncation=0.10,
    )
    assert scores["score"].abs().max() <= 1.0
    assert diagnostics["score"].abs().max() <= 1.0


def test_apply_signal_decay_smooths_recent_scores() -> None:
    panel = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA", "AAA"],
            "trade_date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]).date,
            "score": [0.0, 0.5, 1.0],
        }
    )
    decayed = apply_signal_decay(panel, decay_days=3)
    assert decayed.iloc[-1]["score"] < 1.0
    assert decayed.iloc[-1]["score"] > 0.5


def test_compute_alpha_score_panel_returns_latest_scores_for_registry_alpha() -> None:
    panel, diagnostics = compute_alpha_score_panel(
        "rev_close_3d",
        _bars_fixture(),
        classifications=_classifications_fixture(),
        group_level="sector",
        params={"lookback": 3},
        min_scored_symbols=2,
    )
    assert not panel.empty
    assert not diagnostics.empty
    latest_date = panel["trade_date"].max()
    latest = panel[panel["trade_date"] == latest_date]
    assert latest["score"].notna().all()


def test_compute_alpha_panel_supports_literature_templates() -> None:
    panel = compute_alpha_panel(
        "skip_month_momentum",
        _bars_fixture(),
        classifications=_classifications_fixture(),
        group_level="sector",
        params={"lookback": 126, "skip": 21},
    )
    latest = panel[panel["trade_date"] == panel["trade_date"].max()]
    assert latest["alpha_raw"].notna().all()

    low_vol_panel = compute_alpha_panel(
        "low_volatility_defensive",
        _bars_fixture(),
        classifications=_classifications_fixture(),
        group_level="sector",
        params={"window": 63},
    )
    assert low_vol_panel["alpha_raw"].notna().any()
