from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import pandas as pd


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

from historical_store import HistoricalStore  # type: ignore  # noqa: E402


class _FakeBroker:
    def __init__(self) -> None:
        self.bar_calls = 0
        self.calendar_calls = 0

    def get_daily_bars(
        self,
        symbols: list[str],
        *,
        start: date,
        end: date,
        timeframe: str,
        adjustment: str,
        limit: int,
        feed: str,
    ) -> pd.DataFrame:
        self.bar_calls += 1
        rows: list[dict[str, object]] = []
        for symbol in symbols:
            rows.append(
                {
                    "symbol": symbol,
                    "t": f"{start.isoformat()}T21:00:00Z",
                    "o": 10.0,
                    "h": 11.0,
                    "l": 9.0,
                    "c": 10.5,
                    "v": 1000,
                    "vw": 10.2,
                    "n": 1,
                }
            )
        return pd.DataFrame(rows)

    def list_trading_days(self, start: date, end: date) -> list[date]:
        self.calendar_calls += 1
        return [start, end]


def test_load_bars_reuses_cached_year_partition(tmp_path: Path) -> None:
    broker = _FakeBroker()
    store = HistoricalStore(tmp_path / "cache")

    out_first = store.load_bars(
        ["AAA", "BBB"],
        start=date(2026, 1, 2),
        end=date(2026, 1, 2),
        broker=broker,
        feed="iex",
        adjustment="split,spin-off",
    )
    out_second = store.load_bars(
        ["AAA", "BBB"],
        start=date(2026, 1, 2),
        end=date(2026, 1, 2),
        broker=broker,
        feed="iex",
        adjustment="split,spin-off",
    )

    assert broker.bar_calls == 1
    assert len(out_first) == 2
    assert len(out_second) == 2
    assert store.bar_path(feed="iex", adjustment="split,spin-off", year=2026).exists()


def test_load_trading_calendar_caches_dates(tmp_path: Path) -> None:
    broker = _FakeBroker()
    store = HistoricalStore(tmp_path / "cache")

    first = store.load_trading_calendar(
        start=date(2026, 1, 2),
        end=date(2026, 1, 5),
        broker=broker,
    )
    second = store.load_trading_calendar(
        start=date(2026, 1, 2),
        end=date(2026, 1, 5),
        broker=broker,
    )

    assert first == second
    assert broker.calendar_calls >= 1
    assert store.calendar_path().exists()


def test_load_bars_refetches_when_cached_year_range_is_incomplete(tmp_path: Path) -> None:
    broker = _FakeBroker()
    store = HistoricalStore(tmp_path / "cache")

    first = store.load_bars(
        ["AAA"],
        start=date(2025, 5, 5),
        end=date(2025, 5, 5),
        broker=broker,
        feed="iex",
        adjustment="split,spin-off",
    )
    second = store.load_bars(
        ["AAA"],
        start=date(2025, 3, 12),
        end=date(2025, 5, 5),
        broker=broker,
        feed="iex",
        adjustment="split,spin-off",
    )

    assert broker.bar_calls == 2
    assert pd.to_datetime(first["trade_date"]).dt.date.min() == date(2025, 5, 5)
    assert pd.to_datetime(second["trade_date"]).dt.date.min() == date(2025, 3, 12)
