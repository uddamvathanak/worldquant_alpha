from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import pandas as pd
import pytest


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from reference_data import (  # type: ignore  # noqa: E402
    ReferenceDataError,
    group_pct_rank,
    load_classifications_asof,
    load_fundamentals_asof,
)


def test_load_fundamentals_asof_selects_latest_prior_and_ignores_future_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fundamentals.csv"
    pd.DataFrame(
        [
            {
                "symbol": "aaa",
                "effective_date": "2026-01-01",
                "fnd2_ebitdm": 1.0,
                "fnd2_ebitfr": 2.0,
                "fn_assets_fair_val_a": 3.0,
            },
            {
                "symbol": "AAA",
                "effective_date": "2026-02-01",
                "fnd2_ebitdm": 4.0,
                "fnd2_ebitfr": 5.0,
                "fn_assets_fair_val_a": 6.0,
            },
            {
                "symbol": "BBB",
                "effective_date": "2026-04-01",
                "fnd2_ebitdm": 7.0,
                "fnd2_ebitfr": 8.0,
                "fn_assets_fair_val_a": 9.0,
            },
        ]
    ).to_csv(path, index=False)

    out = load_fundamentals_asof(
        ["AAA", "BBB"],
        date(2026, 2, 15),
        path,
        freshness_days=180,
    )

    aaa = out[out["symbol"] == "AAA"].iloc[0]
    bbb = out[out["symbol"] == "BBB"].iloc[0]
    assert float(aaa["fnd2_ebitdm"]) == 4.0
    assert aaa["effective_date"] == date(2026, 2, 1)
    assert bool(aaa["is_stale"]) is False
    assert pd.isna(bbb["effective_date"])
    assert bool(bbb["is_stale"]) is True


def test_load_fundamentals_asof_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "fundamentals.csv"
    pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "effective_date": "2026-02-01",
                "fnd2_ebitdm": 1.0,
                "fnd2_ebitfr": 2.0,
                "fn_assets_fair_val_a": 3.0,
            },
            {
                "symbol": "AAA",
                "effective_date": "2026-02-01",
                "fnd2_ebitdm": 4.0,
                "fnd2_ebitfr": 5.0,
                "fn_assets_fair_val_a": 6.0,
            },
        ]
    ).to_csv(path, index=False)

    with pytest.raises(ReferenceDataError):
        load_fundamentals_asof(["AAA"], date(2026, 2, 15), path)


def test_load_classifications_asof_selects_latest_prior(tmp_path: Path) -> None:
    path = tmp_path / "classifications.csv"
    pd.DataFrame(
        [
            {"symbol": "AAA", "effective_date": "2026-01-01", "sector": "Tech", "industry": "Software"},
            {"symbol": "AAA", "effective_date": "2026-03-01", "sector": "Tech", "industry": "Infra"},
            {"symbol": "BBB", "effective_date": "2026-01-15", "sector": "Health", "industry": "Biotech"},
        ]
    ).to_csv(path, index=False)

    out = load_classifications_asof(["AAA", "BBB"], date(2026, 2, 15), path)
    aaa = out[out["symbol"] == "AAA"].iloc[0]
    bbb = out[out["symbol"] == "BBB"].iloc[0]
    assert aaa["industry"] == "Software"
    assert bbb["industry"] == "Biotech"


def test_group_pct_rank_handles_ties_and_missing_values() -> None:
    frame = pd.DataFrame(
        {
            "industry": ["A", "A", "A", "B", "B"],
            "value": [1.0, 1.0, None, 2.0, 5.0],
        }
    )
    ranked = group_pct_rank(frame, "value", "industry")

    assert ranked.iloc[0] == pytest.approx(0.75, abs=1e-9)
    assert ranked.iloc[1] == pytest.approx(0.75, abs=1e-9)
    assert pd.isna(ranked.iloc[2])
    assert ranked.iloc[3] == pytest.approx(0.5, abs=1e-9)
    assert ranked.iloc[4] == pytest.approx(1.0, abs=1e-9)
