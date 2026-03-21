from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from research_runner import (  # type: ignore  # noqa: E402
    _apply_sector_vs_none_rule,
    _filter_oos_survivors,
    _filter_unseen_passers,
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
