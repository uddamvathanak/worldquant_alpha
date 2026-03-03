from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from broker_alpaca import AlpacaBroker
from config import load_config, parse_trade_date


class UniverseBuildError(RuntimeError):
    pass


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "yes", "y", "on"}


def normalize_assets_frame(raw_assets: pd.DataFrame) -> pd.DataFrame:
    if raw_assets is None or raw_assets.empty:
        raise UniverseBuildError("Asset list is empty.")

    out = raw_assets.copy()
    out["symbol"] = out.get("symbol", "").astype(str).str.strip().str.upper()
    out = out[out["symbol"] != ""].copy()

    for col in [
        "tradable",
        "shortable",
        "easy_to_borrow",
        "marginable",
        "fractionable",
    ]:
        if col not in out.columns:
            out[col] = False
        out[col] = out[col].map(_to_bool)

    if "exchange" not in out.columns:
        out["exchange"] = ""
    out["exchange"] = out["exchange"].astype(str).str.strip().str.upper()
    out = out.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
    return out


def _normalize_bars(raw_bars: pd.DataFrame) -> pd.DataFrame:
    if raw_bars is None or raw_bars.empty:
        raise UniverseBuildError("No daily bars returned for universe ranking.")

    bars = raw_bars.copy()
    bars["symbol"] = bars["symbol"].astype(str).str.strip().str.upper()
    bars["trade_date"] = pd.to_datetime(bars["t"], utc=True, errors="coerce").dt.date
    bars["c"] = pd.to_numeric(bars["c"], errors="coerce")
    bars["v"] = pd.to_numeric(bars["v"], errors="coerce")
    bars = bars.dropna(subset=["symbol", "trade_date", "c", "v"])
    bars = bars[(bars["c"] > 0) & (bars["v"] > 0)].copy()
    if bars.empty:
        raise UniverseBuildError("Bars are empty after cleaning.")
    return bars


def select_liquid_universe(
    assets: pd.DataFrame,
    bars: pd.DataFrame,
    *,
    asof_date: date,
    lookback_days: int,
    max_symbols: int,
    min_price: float,
    min_dollar_volume: float,
    min_coverage: float,
    shortable_only: bool,
) -> pd.DataFrame:
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive.")
    if max_symbols <= 0:
        raise ValueError("max_symbols must be positive.")
    if min_coverage <= 0 or min_coverage > 1:
        raise ValueError("min_coverage must be in (0, 1].")

    assets_norm = normalize_assets_frame(assets)
    bars_norm = _normalize_bars(bars)

    valid_dates = sorted(d for d in bars_norm["trade_date"].unique().tolist() if d <= asof_date)
    if not valid_dates:
        raise UniverseBuildError(
            f"No bars available on or before {asof_date.isoformat()} for universe ranking."
        )

    lookback_dates = valid_dates[-lookback_days:]
    lookback_count = len(lookback_dates)
    window = bars_norm[bars_norm["trade_date"].isin(lookback_dates)].copy()
    if window.empty:
        raise UniverseBuildError("No bars found in requested lookback window.")

    window["dollar_volume"] = window["c"] * window["v"]
    stats = (
        window.groupby("symbol", as_index=False)
        .agg(
            obs_count=("trade_date", "nunique"),
            avg_close=("c", "mean"),
            avg_dollar_volume=("dollar_volume", "mean"),
        )
        .reset_index(drop=True)
    )
    stats["coverage"] = stats["obs_count"].astype(float) / float(lookback_count)

    merged = assets_norm.merge(stats, on="symbol", how="inner")
    mask = (
        merged["tradable"]
        & (merged["avg_close"] >= float(min_price))
        & (merged["avg_dollar_volume"] >= float(min_dollar_volume))
        & (merged["coverage"] >= float(min_coverage))
    )
    if shortable_only:
        mask &= merged["shortable"]

    out = merged[mask].copy()
    if out.empty:
        raise UniverseBuildError(
            "Universe became empty after filters. Relax min_dollar_volume/min_price/min_coverage."
        )

    out = out.sort_values(
        ["avg_dollar_volume", "coverage", "symbol"],
        ascending=[False, False, True],
    ).head(int(max_symbols))
    out["asof_date"] = asof_date.isoformat()
    out = out[
        [
            "symbol",
            "avg_dollar_volume",
            "avg_close",
            "coverage",
            "obs_count",
            "tradable",
            "shortable",
            "easy_to_borrow",
            "marginable",
            "fractionable",
            "exchange",
            "asof_date",
        ]
    ].reset_index(drop=True)
    return out


def run_universe_build(args: argparse.Namespace) -> Path:
    cfg = load_config()
    trade_date = parse_trade_date(args.date)
    output_path = (
        Path(args.output_file).resolve()
        if args.output_file
        else (cfg.private_dir / "universe.csv")
    )

    api_key, api_secret = cfg.require_alpaca_credentials()
    broker = AlpacaBroker(api_key, api_secret, paper=True)

    assets = broker.list_assets(status="active", asset_class="us_equity")
    assets = normalize_assets_frame(assets)
    candidate_assets = assets[assets["tradable"]].copy()
    symbols = candidate_assets["symbol"].tolist()
    if not symbols:
        raise UniverseBuildError("No tradable symbols returned by Alpaca assets API.")

    lookback_days = max(5, int(args.lookback_days))
    start = trade_date - timedelta(days=lookback_days + 40)
    bars = broker.get_daily_bars(
        symbols,
        start=start,
        end=trade_date,
        timeframe="1Day",
        adjustment="raw",
        limit=10_000,
    )

    universe = select_liquid_universe(
        candidate_assets,
        bars,
        asof_date=trade_date,
        lookback_days=lookback_days,
        max_symbols=max(10, int(args.max_symbols)),
        min_price=float(args.min_price),
        min_dollar_volume=float(args.min_dollar_volume),
        min_coverage=float(args.min_coverage),
        shortable_only=bool(args.shortable_only),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    universe.to_csv(output_path, index=False)

    print(f"universe_file: {output_path}")
    print(f"trade_date: {trade_date.isoformat()}")
    print(f"assets_count: {len(assets)}")
    print(f"tradable_count: {len(candidate_assets)}")
    print(f"selected_count: {len(universe)}")
    print(f"shortable_only: {bool(args.shortable_only)}")
    print(f"lookback_days: {lookback_days}")
    print(f"min_dollar_volume: {float(args.min_dollar_volume):.2f}")
    print(f"min_price: {float(args.min_price):.2f}")
    print("top5:")
    print(universe.head(5)[["symbol", "avg_dollar_volume", "coverage"]].to_string(index=False))
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a liquid US-equity universe from Alpaca assets + daily bars and write universe.csv."
        )
    )
    parser.add_argument("--date", default="", help="As-of trade date in YYYY-MM-DD (ET).")
    parser.add_argument(
        "--output-file",
        default="",
        help="Universe CSV output path (default: paper/alpaca/private/universe.csv).",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=3000,
        help="Maximum symbols to keep after ranking by dollar volume.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=20,
        help="Trading-day lookback for average dollar-volume ranking.",
    )
    parser.add_argument(
        "--min-price",
        type=float,
        default=3.0,
        help="Minimum average close price.",
    )
    parser.add_argument(
        "--min-dollar-volume",
        type=float,
        default=0.0,
        help="Minimum average daily dollar volume (close*volume).",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.80,
        help="Minimum bar-coverage ratio inside lookback window.",
    )
    parser.add_argument(
        "--shortable-only",
        action="store_true",
        help="Keep only shortable symbols (recommended for long/short).",
    )
    parser.add_argument(
        "--include-non-shortable",
        action="store_true",
        help="Override --shortable-only and keep all tradable symbols.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.include_non_shortable:
        args.shortable_only = False
    elif not args.shortable_only:
        # Default behavior is shortable-only for long/short execution reliability.
        args.shortable_only = True
    run_universe_build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
