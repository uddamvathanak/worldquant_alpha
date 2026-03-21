from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import pandas as pd
import pytest


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from classification_store import (  # type: ignore  # noqa: E402
    ClassificationStoreError,
    build_classification_snapshot,
    build_group_key,
    load_classifications_snapshot,
    merge_proxy_classifications,
    write_classification_snapshot,
)


def test_build_classification_snapshot_merges_symbol_changes_and_delisted() -> None:
    profiles = pd.DataFrame(
        [
            {"symbol": "ABC", "sector": "Tech", "industry": "Software"},
            {"symbol": "XYZ", "sector": "Health", "industry": "Biotech"},
        ]
    )
    symbol_changes = pd.DataFrame(
        [
            {"oldSymbol": "ABX", "newSymbol": "ABC", "date": "2024-01-01"},
        ]
    )
    delisted = pd.DataFrame(
        [
            {"symbol": "XYZ", "delistedDate": "2025-12-31"},
        ]
    )

    snapshot, symbol_master = build_classification_snapshot(
        profiles,
        symbol_changes,
        delisted,
        snapshot_date=date(2026, 3, 17),
    )

    abx = snapshot[snapshot["symbol"] == "ABX"].iloc[0]
    xyz = symbol_master[symbol_master["symbol"] == "XYZ"].iloc[0]
    assert abx["canonical_symbol"] == "ABC"
    assert abx["sector"] == "Tech"
    assert abx["industry"] == "Software"
    assert bool(xyz["is_delisted"]) is True
    assert str(xyz["delisted_date"]) == "2025-12-31"


def test_write_and_load_classification_snapshot_round_trip(tmp_path: Path) -> None:
    snapshot = pd.DataFrame(
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
            }
        ]
    )
    symbol_master = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "canonical_symbol": "AAA",
                "original_symbol": "AAA",
                "first_seen_date": "2026-03-17",
                "last_seen_date": "2026-03-17",
                "is_delisted": False,
                "delisted_date": "",
                "source": "fmp",
            }
        ]
    )
    reference_dir = tmp_path / "reference"
    write_classification_snapshot(
        snapshot,
        symbol_master,
        reference_dir=reference_dir,
        snapshot_date=date(2026, 3, 17),
    )

    loaded = load_classifications_snapshot(reference_dir, snapshot_date=date(2026, 3, 17))
    assert len(loaded) == 1
    assert loaded.iloc[0]["sector"] == "Tech"
    assert loaded.iloc[0]["industry"] == "Software"


def test_load_classifications_snapshot_requires_bootstrap(tmp_path: Path) -> None:
    with pytest.raises(ClassificationStoreError):
        load_classifications_snapshot(tmp_path)


def test_merge_proxy_classifications_and_group_key_fallbacks() -> None:
    base = pd.DataFrame({"symbol": ["AAA", "BBB", "CCC"]})
    classifications = pd.DataFrame(
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
                "sector": "Energy",
                "industry": "",
                "is_delisted": False,
                "delisted_date": "",
                "original_symbol": "BBB",
                "source": "fmp",
            },
        ]
    )
    merged = merge_proxy_classifications(
        base,
        classifications=classifications,
        sector_map={"CCC": "Utilities"},
    )
    keys = build_group_key(merged, group_level="auto").tolist()
    assert keys == ["Software", "Energy", "Utilities"]
    market_keys = build_group_key(merged, group_level="market").tolist()
    assert market_keys == ["MARKET", "MARKET", "MARKET"]
