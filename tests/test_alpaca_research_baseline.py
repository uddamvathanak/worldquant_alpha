from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from research_baseline import load_research_baseline  # type: ignore  # noqa: E402
from research_runner import _resolve_research_args  # type: ignore  # noqa: E402


def test_load_research_baseline_reads_committed_defaults() -> None:
    baseline = load_research_baseline(ALPACA_DIR / "research_baseline.json")
    assert baseline.feed == "sip"
    assert baseline.train_days == 756
    assert baseline.oos_days == 252
    assert baseline.test_days == 252
    assert baseline.top_n_grid == [3000]


def test_resolve_research_args_uses_baseline_defaults() -> None:
    cfg = SimpleNamespace(research_baseline_file=ALPACA_DIR / "research_baseline.json")
    args = SimpleNamespace(
        baseline_file="",
        dynamic_baseline=False,
        end_date="",
        feed="",
        train_days=0,
        oos_days=0,
        test_days=0,
        alpha_set="",
        group_level_grid="",
        book_mode_grid="",
        top_n_grid="",
        decay_grid="",
        truncation_grid="",
    )
    resolved = _resolve_research_args(cfg, args)
    assert resolved["feed"] == "sip"
    assert resolved["train_days"] == 756
    assert resolved["alpha_set"] == "literature_core"
    assert resolved["classification_snapshot_date"].isoformat() == "2026-03-17"
