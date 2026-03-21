from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from portfolio_builder import build_sector_neutral_targets  # type: ignore  # noqa: E402


def test_sector_weighted_book_uses_full_universe_and_stays_sector_neutral() -> None:
    signals = pd.DataFrame(
        {
            "symbol": ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"],
            "sector": ["A", "A", "A", "A", "B", "B", "B", "B"],
            "score": [1.0, 2.0, 3.0, 4.0, 10.0, 11.0, 12.0, 13.0],
        }
    )

    build = build_sector_neutral_targets(
        signals,
        equity=100_000.0,
        top_n=3000,
        gross_exposure=4.0,
        book_mode="sector_weighted",
        shortable_map=None,
    )
    targets = build.targets

    assert len(targets) == 8
    assert int((targets["side"] == "long").sum()) == 4
    assert int((targets["side"] == "short").sum()) == 4

    long_gross = float(targets.loc[targets["side"] == "long", "target_weight"].sum())
    short_gross = float(-targets.loc[targets["side"] == "short", "target_weight"].sum())
    assert round(long_gross, 8) == 2.0
    assert round(short_gross, 8) == 2.0

    sector_weights = (
        targets.groupby(["sector", "side"], as_index=False)["target_weight"].sum()
        .pivot(index="sector", columns="side", values="target_weight")
        .fillna(0.0)
    )
    assert round(float(sector_weights.loc["A", "long"]), 8) == round(abs(float(sector_weights.loc["A", "short"])), 8)
    assert round(float(sector_weights.loc["B", "long"]), 8) == round(abs(float(sector_weights.loc["B", "short"])), 8)

