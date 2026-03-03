from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from broker_alpaca import AlpacaBroker
from config import load_config, parse_trade_date
from signal_loader import signal_path_for_date


class SignalGenerationError(RuntimeError):
    pass


def _read_symbol_list_from_csv(path: Path) -> list[str]:
    frame = pd.read_csv(path)
    if frame.empty:
        return []

    lower = {str(col).strip().lower(): col for col in frame.columns}
    if "symbol" in lower:
        series = frame[lower["symbol"]]
    else:
        series = frame.iloc[:, 0]

    symbols = (
        series.astype(str)
        .str.strip()
        .str.upper()
        .replace("", pd.NA)
        .dropna()
        .tolist()
    )
    return sorted(set(symbols))


def _latest_signal_file(signals_dir: Path) -> Path | None:
    if not signals_dir.exists():
        return None
    files = [
        p
        for p in signals_dir.glob("*.csv")
        if p.name.lower() != ".gitkeep" and p.is_file()
    ]
    if not files:
        return None
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def load_universe_symbols(
    *,
    universe_file: Path,
    signals_dir: Path,
) -> list[str]:
    if universe_file.exists():
        symbols = _read_symbol_list_from_csv(universe_file)
        if symbols:
            return symbols

    latest_signal = _latest_signal_file(signals_dir)
    if latest_signal is not None:
        frame = pd.read_csv(latest_signal)
        lower = {str(col).strip().lower(): col for col in frame.columns}
        if "symbol" in lower:
            symbols = (
                frame[lower["symbol"]]
                .astype(str)
                .str.strip()
                .str.upper()
                .replace("", pd.NA)
                .dropna()
                .tolist()
            )
            symbols = sorted(set(symbols))
            if symbols:
                return symbols

    raise SignalGenerationError(
        "No universe symbols available. Provide paper/alpaca/private/universe.csv "
        "with a 'symbol' column or keep at least one prior signals CSV."
    )


def load_sector_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    frame = pd.read_csv(path)
    if frame.empty:
        return {}

    lower = {str(col).strip().lower(): col for col in frame.columns}
    if "symbol" not in lower or "sector" not in lower:
        raise SignalGenerationError(
            f"Sector map {path} must include columns: symbol, sector"
        )

    out: dict[str, str] = {}
    for _, row in frame.iterrows():
        symbol = str(row[lower["symbol"]]).strip().upper()
        sector = str(row[lower["sector"]]).strip()
        if symbol and sector:
            out[symbol] = sector
    return out


def _normalize_bars(raw_bars: pd.DataFrame) -> pd.DataFrame:
    if raw_bars.empty:
        raise SignalGenerationError("No daily bars returned from Alpaca data API.")

    bars = raw_bars.copy()
    bars["symbol"] = bars["symbol"].astype(str).str.strip().str.upper()
    bars["trade_date"] = pd.to_datetime(bars["t"], utc=True, errors="coerce").dt.date

    for col in ["o", "h", "l", "c", "v", "vw"]:
        bars[col] = pd.to_numeric(bars[col], errors="coerce")

    bars = bars.dropna(subset=["symbol", "trade_date", "o", "h", "l", "c", "v"])
    bars = bars[bars["o"] > 0]
    bars = bars[bars["c"] > 0]
    bars = bars[bars["h"] > 0]
    bars = bars[bars["l"] > 0]
    bars["vw"] = bars["vw"].where(bars["vw"] > 0, bars["c"])
    bars = bars.sort_values(["trade_date", "symbol"]).reset_index(drop=True)

    if bars.empty:
        raise SignalGenerationError("Bars are empty after cleaning.")
    return bars


def compute_failed_move_vwap_scores(
    bars: pd.DataFrame,
    *,
    smoothing: int = 2,
) -> pd.DataFrame:
    if smoothing <= 0:
        raise ValueError("smoothing must be positive.")

    data = _normalize_bars(bars)
    data["id"] = (data["c"] - data["o"]) / data["o"]
    data["ext"] = (data["c"] - data["vw"]) / data["vw"]

    data["id_rank"] = data.groupby("trade_date")["id"].rank(method="average", pct=True)
    data["ext_rank"] = data.groupby("trade_date")["ext"].rank(method="average", pct=True)
    data["combo"] = -data["id_rank"] * data["ext_rank"]

    data = data.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    data["score"] = (
        data.groupby("symbol")["combo"]
        .transform(lambda s: s.rolling(window=smoothing, min_periods=smoothing).mean())
    )

    latest_date = data["trade_date"].max()
    out = data[data["trade_date"] == latest_date][["symbol", "score"]].copy()
    out = out.dropna(subset=["score"])
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    if out.empty:
        raise SignalGenerationError(
            "No scores produced at the latest trade_date. Increase lookback window."
        )
    return out


def build_signal_frame(
    scores: pd.DataFrame,
    *,
    trade_date: date,
    sector_map: dict[str, str] | None = None,
    default_sector: str = "ALL",
) -> pd.DataFrame:
    if scores.empty:
        raise SignalGenerationError("Score frame is empty.")

    sectors = sector_map or {}
    out = scores.copy()
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out["score"] = pd.to_numeric(out["score"], errors="coerce")
    out = out.dropna(subset=["score"])
    out["sector"] = out["symbol"].map(lambda s: sectors.get(s, default_sector))
    out["sector"] = out["sector"].astype(str).str.strip()
    out.loc[out["sector"] == "", "sector"] = default_sector
    out["asof_date"] = trade_date.isoformat()
    out = out[["symbol", "score", "sector", "asof_date"]]
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    return out


def run_signal_generation(args: argparse.Namespace) -> Path:
    cfg = load_config()
    if args.signals_dir:
        cfg.signals_dir = Path(args.signals_dir).resolve()
    cfg.ensure_runtime_dirs()

    trade_date = parse_trade_date(args.date)
    universe_file = (
        Path(args.universe_file).resolve()
        if args.universe_file
        else (cfg.private_dir / "universe.csv")
    )
    sector_map_file = (
        Path(args.sector_map_file).resolve()
        if args.sector_map_file
        else (cfg.private_dir / "sector_map.csv")
    )

    symbols = load_universe_symbols(
        universe_file=universe_file,
        signals_dir=cfg.signals_dir,
    )
    if len(symbols) < 50:
        raise SignalGenerationError(
            f"Universe too small ({len(symbols)} symbols). Provide a broader universe."
        )

    api_key, api_secret = cfg.require_alpaca_credentials()
    broker = AlpacaBroker(api_key, api_secret, paper=True)
    lookback_days = max(5, int(args.lookback_days))
    start = trade_date - timedelta(days=lookback_days + 40)

    raw_bars = broker.get_daily_bars(
        symbols,
        start=start,
        end=trade_date,
        timeframe="1Day",
        adjustment="raw",
        limit=1000,
    )
    scores = compute_failed_move_vwap_scores(
        raw_bars,
        smoothing=max(1, int(args.smoothing)),
    )
    sector_map = load_sector_map(sector_map_file)
    signal_frame = build_signal_frame(
        scores,
        trade_date=trade_date,
        sector_map=sector_map,
        default_sector="ALL",
    )

    output_path = (
        Path(args.output_file).resolve()
        if args.output_file
        else signal_path_for_date(cfg.signals_dir, trade_date)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    signal_frame.to_csv(output_path, index=False)

    print(f"signal_file: {output_path}")
    print(f"trade_date: {trade_date.isoformat()}")
    print(f"universe_count: {len(symbols)}")
    print(f"scored_count: {len(signal_frame)}")
    print("model: failed_move_vwap")
    print("top5:")
    print(signal_frame.head(5).to_string(index=False))
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate daily Alpaca signal CSV from Alpaca market data."
    )
    parser.add_argument("--date", default="", help="Trade date in YYYY-MM-DD (ET).")
    parser.add_argument("--signals-dir", default="", help="Override signals directory.")
    parser.add_argument(
        "--output-file",
        default="",
        help="Optional explicit output CSV path.",
    )
    parser.add_argument(
        "--universe-file",
        default="",
        help="Universe CSV path (default: paper/alpaca/private/universe.csv).",
    )
    parser.add_argument(
        "--sector-map-file",
        default="",
        help="Optional sector map CSV with columns symbol,sector.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=60,
        help="Calendar lookback days for daily bars fetch.",
    )
    parser.add_argument(
        "--smoothing",
        type=int,
        default=2,
        help="Rolling smoothing window for combo signal.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run_signal_generation(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
