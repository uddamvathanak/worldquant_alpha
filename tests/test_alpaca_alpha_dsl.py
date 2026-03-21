from __future__ import annotations

from pathlib import Path
import sys

import pytest


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from alpha_dsl import (  # type: ignore  # noqa: E402
    AlphaDslError,
    candidate_signature,
    normalize_candidate_payload,
    validate_candidate_batch,
)


def test_normalize_candidate_payload_accepts_valid_registry_candidate() -> None:
    candidate = normalize_candidate_payload(
        {
            "template_name": "smooth_momentum",
            "family": "momentum",
            "params": {"window": 42},
            "group_level": "sector",
            "book_mode": "sector",
            "top_n": 50,
            "signal_decay": 3,
            "score_truncation": 0.05,
            "source": "mutation",
            "parent_candidates": ["seed_a"],
            "notes": "narrower momentum window",
        }
    )

    assert candidate.template_name == "smooth_momentum"
    assert candidate.params["window"] == 42
    assert candidate.signal_decay == 3
    assert candidate.score_truncation == 0.05


def test_normalize_candidate_payload_rejects_out_of_bounds_values() -> None:
    with pytest.raises(AlphaDslError):
        normalize_candidate_payload(
            {
                "template_name": "smooth_momentum",
                "family": "momentum",
                "params": {"window": 999},
                "group_level": "sector",
                "book_mode": "sector",
                "top_n": 50,
                "signal_decay": 3,
                "score_truncation": 0.05,
                "source": "mutation",
                "parent_candidates": [],
                "notes": "",
            }
        )


def test_validate_candidate_batch_dedupes_equivalent_candidates() -> None:
    payload = [
        {
            "template_name": "vwap_extreme_revert",
            "family": "vwap_reversion",
            "params": {"window": 3},
            "group_level": "industry",
            "book_mode": "sector",
            "top_n": 30,
            "signal_decay": 0,
            "score_truncation": None,
            "source": "mutation",
            "parent_candidates": ["seed_a"],
            "notes": "",
        },
        {
            "template_name": "vwap_extreme_revert",
            "family": "vwap_reversion",
            "params": {"window": 3},
            "group_level": "industry",
            "book_mode": "sector",
            "top_n": 30,
            "signal_decay": 0,
            "score_truncation": None,
            "source": "mutation",
            "parent_candidates": ["seed_b"],
            "notes": "",
        },
    ]

    out = validate_candidate_batch(payload)

    assert len(out) == 1
    assert candidate_signature(out[0].to_dict()).startswith("vwap_extreme_revert|")

