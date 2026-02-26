from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import pandas as pd
import pytest


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from execution import (  # type: ignore  # noqa: E402
    build_order_plan,
    execute_order_plan,
    extract_rejected_short_symbols,
)
from monthly_eval import compute_proxy_metrics  # type: ignore  # noqa: E402
from portfolio_builder import (  # type: ignore  # noqa: E402
    build_sector_neutral_targets,
    drop_and_rescale_rejected_shorts,
    portfolio_exposure,
)
from signal_loader import SignalValidationError, validate_signal_frame  # type: ignore  # noqa: E402


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


def test_rejected_shorts_are_detected_and_rescaled() -> None:
    targets = pd.DataFrame(
        {
            "symbol": ["L1", "S1", "S2"],
            "side": ["long", "short", "short"],
            "sector": ["Tech", "Tech", "Tech"],
            "score": [1.0, -1.0, -0.9],
            "target_weight": [0.4, -0.2, -0.2],
            "target_notional": [40_000.0, -20_000.0, -20_000.0],
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

    corrected = drop_and_rescale_rejected_shorts(
        targets,
        rejected_symbols=rejected,
        short_gross_target=0.4,
    )
    short_side = corrected[corrected["side"] == "short"]
    assert len(short_side) == 1
    assert pytest.approx(float(short_side["target_weight"].sum()), abs=1e-9) == -0.4


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

