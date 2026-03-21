from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import pandas as pd
import pytest


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from alpha_registry import StrategyMember, StrategySpec, write_strategy_spec  # type: ignore  # noqa: E402
from execution import (  # type: ignore  # noqa: E402
    apply_margin_guard,
    augment_targets_with_flat_positions,
    build_order_plan,
    execute_order_plan,
    extract_rejected_symbols,
    extract_rejected_short_symbols,
)
import daily_pipeline  # type: ignore  # noqa: E402
from monthly_eval import compute_proxy_metrics  # type: ignore  # noqa: E402
from portfolio_builder import (  # type: ignore  # noqa: E402
    build_sector_neutral_targets,
    drop_rejected_shorts_and_reneutralize,
    portfolio_exposure,
)
from signal_loader import (  # type: ignore  # noqa: E402
    SignalValidationError,
    select_long_short_candidates,
    validate_signal_frame,
)


def test_signal_validation_missing_columns_and_duplicates() -> None:
    with pytest.raises(SignalValidationError):
        validate_signal_frame(pd.DataFrame({"symbol": ["AAPL"], "score": [1.0]}))

    dupes = pd.DataFrame(
        {
            "symbol": ["aapl", "AAPL"],
            "score": [1.0, -1.0],
            "sector": ["Tech", "Tech"],
        }
    )
    with pytest.raises(SignalValidationError):
        validate_signal_frame(dupes)


def test_signal_validation_asof_date() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT"],
            "score": [0.4, -0.3],
            "sector": ["Tech", "Tech"],
            "asof_date": ["2026-02-25", "2026-02-25"],
        }
    )
    out = validate_signal_frame(frame, trade_date=date(2026, 2, 25))
    assert list(out.columns) == ["symbol", "score", "sector", "asof_date"]

    with pytest.raises(SignalValidationError):
        validate_signal_frame(frame, trade_date=date(2026, 2, 24))


def test_portfolio_builder_sector_neutral_exposure() -> None:
    signals = pd.DataFrame(
        {
            "symbol": [
                "A1",
                "A2",
                "A3",
                "A4",
                "B1",
                "B2",
                "B3",
                "B4",
                "C1",
                "C2",
            ],
            "score": [0.9, 0.8, 0.6, 0.5, -0.9, -0.8, -0.6, -0.5, 0.2, -0.2],
            "sector": [
                "Tech",
                "Tech",
                "Health",
                "Health",
                "Tech",
                "Tech",
                "Health",
                "Health",
                "Utilities",
                "Utilities",
            ],
        }
    )
    shortable = {symbol: True for symbol in signals["symbol"]}
    built = build_sector_neutral_targets(
        signals,
        equity=100_000.0,
        top_n=4,
        gross_exposure=0.80,
        shortable_map=shortable,
    )

    targets = built.targets
    exposure = portfolio_exposure(targets)

    assert pytest.approx(exposure["long_gross"], abs=1e-9) == 0.40
    assert pytest.approx(exposure["short_gross"], abs=1e-9) == 0.40
    assert pytest.approx(exposure["net"], abs=1e-9) == 0.0

    long_sectors = set(targets.loc[targets["side"] == "long", "sector"])
    short_sectors = set(targets.loc[targets["side"] == "short", "sector"])
    assert long_sectors == short_sectors


def test_portfolio_builder_none_book_mode_uses_raw_balanced_selection() -> None:
    signals = pd.DataFrame(
        {
            "symbol": ["L1", "L2", "L3", "S1", "S2", "S3"],
            "score": [0.9, 0.8, 0.7, -0.9, -0.8, -0.7],
            "sector": ["Tech", "Health", "Utilities", "Tech", "Tech", "Health"],
        }
    )
    shortable = {symbol: True for symbol in signals["symbol"]}
    built = build_sector_neutral_targets(
        signals,
        equity=100_000.0,
        top_n=3,
        gross_exposure=0.80,
        book_mode="none",
        shortable_map=shortable,
    )

    targets = built.targets
    assert built.stats["book_mode"] == "none"
    assert targets[targets["side"] == "long"]["symbol"].tolist() == ["L1", "L2", "L3"]
    assert targets[targets["side"] == "short"]["symbol"].tolist() == ["S1", "S2", "S3"]
    assert pytest.approx(float(targets["target_weight"].sum()), abs=1e-9) == 0.0


def test_long_short_candidate_selection_avoids_overlap_on_ties() -> None:
    signals = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB", "CCC", "DDD"],
            "score": [0.0, 0.0, 0.0, 0.0],
            "sector": ["Tech", "Tech", "Health", "Health"],
        }
    )

    longs, shorts = select_long_short_candidates(signals, top_n=2)
    assert set(longs["symbol"]) == {"AAA", "BBB"}
    assert set(shorts["symbol"]) == {"CCC", "DDD"}
    assert set(longs["symbol"]).isdisjoint(set(shorts["symbol"]))


class _DummyBroker:
    def submit_market_notional_order(
        self,
        *,
        symbol: str,
        side: str,
        notional: float,
        client_order_id: str,
    ) -> dict[str, str]:
        if symbol == "S2":
            raise RuntimeError("short rejected")
        return {"order_id": client_order_id, "status": "new"}

    def submit_market_qty_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        client_order_id: str,
    ) -> dict[str, str]:
        if symbol == "S2":
            raise RuntimeError("short rejected")
        return {"order_id": client_order_id, "status": "new"}


def test_rejected_shorts_are_detected_and_reneutralized() -> None:
    targets = pd.DataFrame(
        {
            "symbol": ["L1", "S1", "S2"],
            "side": ["long", "short", "short"],
            "sector": ["Tech", "Tech", "Tech"],
            "score": [1.0, -1.0, -0.9],
            "target_weight": [0.5, -0.2, -0.2],
            "target_notional": [50_000.0, -20_000.0, -20_000.0],
        }
    )
    positions = pd.DataFrame(
        columns=[
            "symbol",
            "qty",
            "side",
            "market_value",
            "signed_market_value",
            "avg_entry_price",
            "unrealized_pl",
        ]
    )
    plan = build_order_plan(targets, positions, min_order_notional=100.0)
    events = execute_order_plan(
        _DummyBroker(),
        plan,
        run_id="run123",
        pass_num=1,
        dry_run=False,
    )
    rejected = extract_rejected_short_symbols(events)
    assert rejected == ["S2"]

    corrected = drop_rejected_shorts_and_reneutralize(
        targets,
        rejected_symbols=rejected,
        short_gross_target=0.4,
    )
    short_side = corrected[corrected["side"] == "short"]
    long_side = corrected[corrected["side"] == "long"]
    assert len(short_side) == 1
    assert len(long_side) == 1
    assert pytest.approx(float(short_side["target_weight"].sum()), abs=1e-9) == -0.4
    assert pytest.approx(float(long_side["target_weight"].sum()), abs=1e-9) == 0.4


def test_extract_rejected_symbols_supports_generic_and_side_filters() -> None:
    events = pd.DataFrame(
        {
            "symbol": ["L1", "S1", "L2"],
            "target_side": ["long", "short", "long"],
            "status": ["new", "rejected", "error"],
        }
    )
    assert extract_rejected_symbols(events) == ["L2", "S1"]
    assert extract_rejected_short_symbols(events) == ["S1"]


def test_all_shorts_rejected_zeroes_long_side() -> None:
    targets = pd.DataFrame(
        {
            "symbol": ["L1", "L2", "S1", "S2"],
            "side": ["long", "long", "short", "short"],
            "sector": ["Tech", "Health", "Tech", "Health"],
            "score": [1.1, 1.0, -1.1, -1.0],
            "target_weight": [0.2, 0.2, -0.2, -0.2],
            "target_notional": [20_000.0, 20_000.0, -20_000.0, -20_000.0],
        }
    )
    corrected = drop_rejected_shorts_and_reneutralize(
        targets,
        rejected_symbols=["S1", "S2"],
        short_gross_target=0.4,
    )
    assert corrected[corrected["side"] == "short"].empty
    assert pytest.approx(
        float(corrected.loc[corrected["side"] == "long", "target_weight"].sum()),
        abs=1e-9,
    ) == 0.0


def test_augment_targets_with_flat_positions_adds_dropped_symbols() -> None:
    core_targets = pd.DataFrame(
        {
            "symbol": ["L1", "S1"],
            "side": ["long", "short"],
            "sector": ["Tech", "Tech"],
            "score": [1.0, -1.0],
            "target_weight": [0.4, -0.4],
            "target_notional": [40_000.0, -40_000.0],
        }
    )
    positions = pd.DataFrame(
        {
            "symbol": ["L1", "OLD"],
            "qty": [100.0, 10.0],
            "side": ["long", "long"],
            "market_value": [40_000.0, 500.0],
            "signed_market_value": [40_000.0, 500.0],
            "avg_entry_price": [400.0, 50.0],
            "unrealized_pl": [0.0, 0.0],
        }
    )
    effective = augment_targets_with_flat_positions(core_targets, positions)
    flat_rows = effective[effective["side"] == "flat"]
    assert len(flat_rows) == 1
    assert flat_rows.iloc[0]["symbol"] == "OLD"
    assert bool(flat_rows.iloc[0]["force_order"]) is True


def test_flat_orders_bypass_min_notional_filter() -> None:
    core_targets = pd.DataFrame(
        {
            "symbol": ["L1", "S1"],
            "side": ["long", "short"],
            "sector": ["Tech", "Tech"],
            "score": [1.0, -1.0],
            "target_weight": [0.4, -0.4],
            "target_notional": [40_000.0, -40_000.0],
        }
    )
    positions = pd.DataFrame(
        {
            "symbol": ["L1", "DUST"],
            "qty": [100.0, 0.1],
            "side": ["long", "long"],
            "market_value": [40_000.0, 5.0],
            "signed_market_value": [40_000.0, 5.0],
            "avg_entry_price": [400.0, 50.0],
            "unrealized_pl": [0.0, 0.0],
        }
    )
    effective = augment_targets_with_flat_positions(core_targets, positions)
    plan = build_order_plan(
        effective,
        positions,
        min_order_notional=100.0,
        price_map={"S1": 200.0},
    )
    dust_rows = plan[plan["symbol"] == "DUST"]
    assert len(dust_rows) == 1
    assert dust_rows.iloc[0]["target_side"] == "flat"
    assert dust_rows.iloc[0]["order_side"] == "sell"
    assert float(dust_rows.iloc[0]["order_notional"]) < 100.0


def test_short_side_uses_qty_orders_when_prices_available() -> None:
    targets = pd.DataFrame(
        {
            "symbol": ["L1", "S1"],
            "side": ["long", "short"],
            "sector": ["Tech", "Tech"],
            "score": [1.0, -1.0],
            "target_weight": [0.4, -0.4],
            "target_notional": [40_000.0, -40_000.0],
        }
    )
    positions = pd.DataFrame(
        columns=[
            "symbol",
            "qty",
            "side",
            "market_value",
            "signed_market_value",
            "avg_entry_price",
            "unrealized_pl",
        ]
    )
    plan = build_order_plan(
        targets,
        positions,
        min_order_notional=100.0,
        price_map={"S1": 200.0},
    )
    short_row = plan[plan["target_side"] == "short"].iloc[0]
    assert int(short_row["order_qty"]) == 200

    events = execute_order_plan(
        _DummyBroker(),
        plan,
        run_id="runqty",
        pass_num=1,
        dry_run=False,
    )
    short_event = events[events["target_side"] == "short"].iloc[0]
    assert int(short_event["order_qty"]) == 200
    assert short_event["status"] == "new"


def test_build_order_plan_prioritizes_derisking_orders_first() -> None:
    targets = pd.DataFrame(
        {
            "symbol": ["L1", "L2"],
            "side": ["long", "long"],
            "sector": ["Tech", "Tech"],
            "score": [0.9, 0.8],
            "target_weight": [0.05, 0.05],
            "target_notional": [5_000.0, 5_000.0],
        }
    )
    positions = pd.DataFrame(
        {
            "symbol": ["L1"],
            "qty": [100.0],
            "side": ["long"],
            "market_value": [10_000.0],
            "signed_market_value": [10_000.0],
            "avg_entry_price": [100.0],
            "unrealized_pl": [0.0],
        }
    )
    plan = build_order_plan(
        targets,
        positions,
        min_order_notional=100.0,
        price_map=None,
    )
    assert len(plan) == 2
    assert plan.iloc[0]["symbol"] == "L1"
    assert plan.iloc[0]["order_side"] == "sell"


def test_margin_guard_scales_incremental_orders_and_keeps_reductions() -> None:
    targets = pd.DataFrame(
        {
            "symbol": ["L1", "L2", "S1"],
            "side": ["long", "long", "short"],
            "sector": ["Tech", "Tech", "Tech"],
            "score": [1.0, 0.8, -1.0],
            "target_weight": [0.05, 0.10, -0.10],
            "target_notional": [5_000.0, 10_000.0, -10_000.0],
        }
    )
    positions = pd.DataFrame(
        {
            "symbol": ["L1"],
            "qty": [100.0],
            "side": ["long"],
            "market_value": [10_000.0],
            "signed_market_value": [10_000.0],
            "avg_entry_price": [100.0],
            "unrealized_pl": [0.0],
        }
    )
    price_map = {"S1": 100.0}
    plan = build_order_plan(
        targets,
        positions,
        min_order_notional=100.0,
        price_map=price_map,
    )
    guarded, summary = apply_margin_guard(
        plan,
        buying_power=6_000.0,
        bp_utilization=1.0,
        margin_buffer_notional=0.0,
        min_order_notional=100.0,
        price_map=price_map,
    )
    assert summary["requested_incremental_notional"] == pytest.approx(20_000.0, abs=1e-6)
    assert summary["available_incremental_notional"] == pytest.approx(6_000.0, abs=1e-6)
    assert summary["scale_applied"] == pytest.approx(0.3, abs=1e-6)

    l1_row = guarded[guarded["symbol"] == "L1"].iloc[0]
    assert float(l1_row["order_notional"]) == pytest.approx(5_000.0, abs=1e-6)
    assert str(l1_row["order_side"]) == "sell"

    post_increase = (
        guarded["target_notional"].astype(float).abs()
        - guarded["current_notional"].astype(float).abs()
    ).clip(lower=0.0).sum()
    assert float(post_increase) <= 6_000.0 + 1e-6


def test_monthly_proxy_metrics_positive_series() -> None:
    rows = []
    equity = 100_000.0
    for idx in range(20):
        daily_return = 0.001
        equity *= 1.0 + daily_return
        rows.append(
            {
                "trade_date": f"2026-01-{idx + 1:02d}",
                "daily_return": daily_return,
                "turnover": 0.10,
                "equity": equity,
            }
        )
    frame = pd.DataFrame(rows)
    summary = compute_proxy_metrics(frame)
    assert summary["sharpe_proxy"] > 0
    assert summary["margin_proxy_bps"] > 0
    assert summary["fitness_proxy"] > 0
    assert summary["returns"] > 0
    assert summary["max_drawdown"] == 0.0


def test_daily_pipeline_passes_model_and_book_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_universe_build(args: object) -> int:
        captured["universe_args"] = args
        return 0

    def fake_run_signal_generation(args: object) -> Path:
        captured["signal_args"] = args
        return Path("C:/tmp/generated_signal.csv")

    def fake_run_rebalance(args: object) -> int:
        captured["rebalance_args"] = args
        return 0

    monkeypatch.setattr(daily_pipeline, "run_universe_build", fake_run_universe_build)
    monkeypatch.setattr(daily_pipeline, "run_signal_generation", fake_run_signal_generation)
    monkeypatch.setattr(daily_pipeline, "run_rebalance", fake_run_rebalance)

    args = daily_pipeline.build_parser().parse_args(
        [
            "--model",
            "profit_asset_gate",
            "--fundamentals-file",
            "paper/alpaca/private/reference/fundamentals.csv",
            "--classifications-file",
            "paper/alpaca/private/reference/classifications.csv",
            "--book-mode",
            "none",
            "--skip-universe-refresh",
        ]
    )
    assert daily_pipeline.run_daily_pipeline(args) == 0

    signal_args = captured["signal_args"]
    rebalance_args = captured["rebalance_args"]
    assert getattr(signal_args, "model") == "profit_asset_gate"
    assert getattr(signal_args, "fundamentals_file").endswith("fundamentals.csv")
    assert getattr(signal_args, "classifications_file").endswith("classifications.csv")
    assert getattr(rebalance_args, "book_mode") == "none"


def test_daily_pipeline_accepts_proxy_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_signal_generation(args: object) -> Path:
        captured["signal_args"] = args
        return Path("C:/tmp/generated_signal.csv")

    def fake_run_rebalance(args: object) -> int:
        captured["rebalance_args"] = args
        return 0

    monkeypatch.setattr(daily_pipeline, "run_signal_generation", fake_run_signal_generation)
    monkeypatch.setattr(daily_pipeline, "run_rebalance", fake_run_rebalance)

    args = daily_pipeline.build_parser().parse_args(
        [
            "--model",
            "profit_asset_gate_proxy",
            "--book-mode",
            "none",
            "--skip-universe-refresh",
        ]
    )
    assert daily_pipeline.run_daily_pipeline(args) == 0

    signal_args = captured["signal_args"]
    rebalance_args = captured["rebalance_args"]
    assert getattr(signal_args, "model") == "profit_asset_gate_proxy"
    assert getattr(rebalance_args, "book_mode") == "none"


def test_daily_pipeline_auto_uses_promoted_selected_strategy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    strategy_path = tmp_path / "selected_strategy.json"
    write_strategy_spec(
        strategy_path,
        StrategySpec(
            strategy_type="single",
            feed="sip",
            gross_exposure=4.0,
            book_mode="sector",
            top_n=75,
            group_level="market",
            members=[
                StrategyMember(
                    name="rev_close_1d__market",
                    alpha_name="rev_close_1d",
                    family="short_reversion",
                    weight=1.0,
                    params={},
                    group_level="market",
                    book_mode="sector",
                    top_n=75,
                    signal_decay=3,
                    score_truncation=0.05,
                )
            ],
            approved=True,
            source_run_id="20260319T101010Z",
        ),
    )

    def fake_load_config() -> object:
        class _Cfg:
            selected_strategy_file = strategy_path

        return _Cfg()

    def fake_run_signal_generation(args: object) -> Path:
        captured["signal_args"] = args
        return Path("C:/tmp/generated_signal.csv")

    def fake_run_rebalance(args: object) -> int:
        captured["rebalance_args"] = args
        return 0

    monkeypatch.setattr(daily_pipeline, "load_config", fake_load_config)
    monkeypatch.setattr(daily_pipeline, "run_signal_generation", fake_run_signal_generation)
    monkeypatch.setattr(daily_pipeline, "run_rebalance", fake_run_rebalance)

    args = daily_pipeline.build_parser().parse_args(["--skip-universe-refresh"])
    assert daily_pipeline.run_daily_pipeline(args) == 0

    signal_args = captured["signal_args"]
    rebalance_args = captured["rebalance_args"]
    assert getattr(signal_args, "model") == "research_selected"
    assert getattr(signal_args, "group_level") == "market"
    assert getattr(rebalance_args, "book_mode") == "sector"
    assert getattr(rebalance_args, "top_n") == 75
