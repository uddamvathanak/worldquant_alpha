from __future__ import annotations

from pathlib import Path
import sys


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from alpha_registry import (  # type: ignore  # noqa: E402
    StrategyMember,
    StrategySpec,
    get_alpha_registry,
    load_strategy_spec,
    write_strategy_spec,
)


def test_alpha_registry_contains_wave_one_families() -> None:
    registry = get_alpha_registry()
    assert "rev_close_1d" in registry
    assert "profit_asset_gate_proxy_v2" in registry
    assert registry["profit_asset_gate_proxy_v2"].parameter_grid["profit_window"] == [42, 63, 84]


def test_strategy_spec_round_trip(tmp_path: Path) -> None:
    strategy = StrategySpec(
        strategy_type="basket",
        feed="sip",
        gross_exposure=4.0,
        book_mode="sector",
        top_n=50,
        group_level="mixed",
        members=[
            StrategyMember(
                name="rev_close_1d__market",
                alpha_name="rev_close_1d",
                family="short_reversion",
                weight=0.5,
                params={},
                group_level="market",
                book_mode="sector",
                top_n=50,
                signal_decay=3,
                score_truncation=0.05,
            ),
            StrategyMember(
                name="smooth_momentum__sector",
                alpha_name="smooth_momentum",
                family="momentum",
                weight=0.5,
                params={"window": 20},
                group_level="sector",
                book_mode="sector",
                top_n=50,
            ),
        ],
        approved=True,
        source_run_id="20260319T010203Z",
    )

    path = tmp_path / "selected_strategy.json"
    write_strategy_spec(path, strategy)
    loaded = load_strategy_spec(path, require_approved=True)
    assert loaded.strategy_type == "basket"
    assert len(loaded.members) == 2
    assert loaded.members[0].signal_decay == 3
    assert loaded.members[0].score_truncation == 0.05
