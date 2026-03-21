from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd


BAR_COLUMNS = ["symbol", "trade_date", "o", "h", "l", "c", "v", "vw", "n"]


class HistoricalStoreError(RuntimeError):
    pass


def _normalize_symbol_list(symbols: Iterable[str]) -> list[str]:
    normalized = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
    return sorted(set(normalized))


def _normalize_bar_frame(raw_bars: pd.DataFrame) -> pd.DataFrame:
    if raw_bars is None or raw_bars.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)

    bars = raw_bars.copy()
    if "trade_date" not in bars.columns and "t" in bars.columns:
        bars["trade_date"] = pd.to_datetime(bars["t"], utc=True, errors="coerce").dt.date
    else:
        bars["trade_date"] = pd.to_datetime(
            bars.get("trade_date", pd.Series(dtype="object")),
            errors="coerce",
        ).dt.date

    bars["symbol"] = bars.get("symbol", pd.Series(dtype="object")).astype(str).str.strip().str.upper()
    for column in ["o", "h", "l", "c", "v", "vw", "n"]:
        bars[column] = pd.to_numeric(bars.get(column, pd.Series(dtype="float64")), errors="coerce")

    bars = bars.dropna(subset=["symbol", "trade_date", "o", "h", "l", "c", "v"])
    bars = bars[bars["symbol"] != ""].copy()
    bars["vw"] = bars["vw"].where(bars["vw"].notna(), bars["c"])
    bars = bars.drop_duplicates(subset=["symbol", "trade_date"], keep="last")
    return bars[BAR_COLUMNS].sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def _adjustment_slug(adjustment: str) -> str:
    text = str(adjustment).strip().lower() or "raw"
    return text.replace(",", "_").replace(" ", "")


@dataclass(slots=True)
class HistoricalStore:
    cache_dir: Path
    bars_root: Path = field(init=False)
    calendar_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.bars_root = self.cache_dir / "bars"
        self.calendar_dir = self.cache_dir / "calendar"
        self.bars_root.mkdir(parents=True, exist_ok=True)
        self.calendar_dir.mkdir(parents=True, exist_ok=True)

    def bar_path(self, *, feed: str, adjustment: str, year: int) -> Path:
        return self.bars_root / str(feed).strip().lower() / _adjustment_slug(adjustment) / f"{year}.parquet"

    def calendar_path(self) -> Path:
        return self.calendar_dir / "trading_days.csv"

    def _read_bar_partition(self, path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=BAR_COLUMNS)
        try:
            return _normalize_bar_frame(pd.read_parquet(path))
        except Exception as exc:
            raise HistoricalStoreError(f"Failed reading cached bar partition {path}: {exc}") from exc

    def _write_bar_partition(self, frame: pd.DataFrame, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        clean = _normalize_bar_frame(frame)
        try:
            clean.to_parquet(path, index=False)
        except ImportError as exc:
            raise HistoricalStoreError(
                "Writing parquet caches requires `pyarrow`. Install it in the runtime environment."
            ) from exc

    def load_bars(
        self,
        symbols: Iterable[str],
        *,
        start: date,
        end: date,
        broker: object,
        feed: str = "iex",
        adjustment: str = "split,spin-off",
    ) -> pd.DataFrame:
        requested_symbols = _normalize_symbol_list(symbols)
        if not requested_symbols:
            return pd.DataFrame(columns=BAR_COLUMNS)
        if end < start:
            return pd.DataFrame(columns=BAR_COLUMNS)

        frames: list[pd.DataFrame] = []
        for year in range(int(start.year), int(end.year) + 1):
            year_start = max(start, date(year, 1, 1))
            year_end = min(end, date(year, 12, 31))
            path = self.bar_path(feed=feed, adjustment=adjustment, year=year)
            cached = self._read_bar_partition(path)
            cached_symbols = set(cached["symbol"].astype(str).unique().tolist())
            cached_min = cached["trade_date"].min() if not cached.empty else None
            cached_max = cached["trade_date"].max() if not cached.empty else None
            range_incomplete = (
                cached.empty
                or cached_min is None
                or cached_max is None
                or cached_min > year_start
                or cached_max < year_end
            )
            missing_symbols = (
                requested_symbols
                if range_incomplete
                else sorted(set(requested_symbols) - cached_symbols)
            )

            if missing_symbols:
                fetched = broker.get_daily_bars(
                    missing_symbols,
                    start=year_start,
                    end=year_end,
                    timeframe="1Day",
                    adjustment=adjustment,
                    limit=10_000,
                    feed=feed,
                )
                fetched_frame = _normalize_bar_frame(fetched)
                merged = (
                    fetched_frame
                    if cached.empty
                    else pd.concat([cached, fetched_frame], ignore_index=True)
                )
                cached = _normalize_bar_frame(merged)
                self._write_bar_partition(cached, path)

            filtered = cached[
                cached["symbol"].isin(requested_symbols)
                & (cached["trade_date"] >= start)
                & (cached["trade_date"] <= end)
            ].copy()
            if not filtered.empty:
                frames.append(filtered)

        if not frames:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return _normalize_bar_frame(pd.concat(frames, ignore_index=True))

    def load_trading_calendar(
        self,
        *,
        start: date,
        end: date,
        broker: object,
    ) -> list[date]:
        if end < start:
            return []

        path = self.calendar_path()
        cached = pd.DataFrame(columns=["trade_date"])
        if path.exists():
            cached = pd.read_csv(path)
            cached["trade_date"] = pd.to_datetime(cached["trade_date"], errors="coerce").dt.date
            cached = cached.dropna(subset=["trade_date"]).drop_duplicates().sort_values("trade_date")

        cached_dates = set(cached["trade_date"].tolist()) if not cached.empty else set()
        expected = pd.bdate_range(start=start, end=end).date.tolist()
        if not all(day in cached_dates for day in expected):
            fetched = broker.list_trading_days(start, end)
            merged = pd.concat(
                [
                    cached,
                    pd.DataFrame({"trade_date": [day.isoformat() for day in fetched]}),
                ],
                ignore_index=True,
            )
            merged["trade_date"] = pd.to_datetime(merged["trade_date"], errors="coerce").dt.date
            merged = merged.dropna(subset=["trade_date"]).drop_duplicates().sort_values("trade_date")
            path.parent.mkdir(parents=True, exist_ok=True)
            merged.to_csv(path, index=False)
            cached = merged

        mask = (cached["trade_date"] >= start) & (cached["trade_date"] <= end)
        return sorted(cached.loc[mask, "trade_date"].tolist())
