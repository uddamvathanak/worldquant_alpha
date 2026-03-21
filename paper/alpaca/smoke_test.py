from __future__ import annotations

import argparse
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pandas as pd
from dotenv import load_dotenv

from config import load_config, parse_trade_date
from rebalance_runner import run_rebalance
from signal_generator import MODEL_CHOICES, load_universe_symbols, run_signal_generation


def _load_env() -> Path:
    env_path = Path(__file__).with_name(".env")
    load_dotenv(dotenv_path=env_path)
    return env_path


def _require_credentials(env_path: Path) -> tuple[str, str]:
    key = os.getenv("APCA_API_KEY_ID")
    secret = os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError(
            f"Missing APCA_API_KEY_ID/APCA_API_SECRET_KEY in {env_path}."
        )
    return key, secret


def run_connectivity_smoke() -> None:
    env_path = _load_env()
    key, secret = _require_credentials(env_path)

    try:
        from alpaca.trading.client import TradingClient
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency `alpaca-py`. Run this script with the `alpaca-paper` "
            "environment or install it with: pip install alpaca-py"
        ) from exc

    client = TradingClient(key, secret, paper=True)
    account = client.get_account()

    print("account_number:", account.account_number)
    print("status:", account.status)
    print("currency:", account.currency)
    print("buying_power:", account.buying_power)
    print("equity:", account.equity)


def _build_smoke_universe(
    *,
    universe_file: Path,
    signals_dir: Path,
    output_file: Path,
    universe_size: int,
) -> Path:
    symbols = load_universe_symbols(
        universe_file=universe_file,
        signals_dir=signals_dir,
    )
    if len(symbols) < universe_size:
        raise RuntimeError(
            f"Universe only has {len(symbols)} symbols, need at least {universe_size}."
        )
    pd.DataFrame({"symbol": symbols[:universe_size]}).to_csv(output_file, index=False)
    return output_file


def run_pipeline_smoke(args: argparse.Namespace) -> None:
    _load_env()
    cfg = load_config()
    trade_date = parse_trade_date(args.date)
    model = str(args.model or cfg.signal_model).strip().lower()
    requested_top_n = int(args.top_n) if args.top_n is not None else int(cfg.top_n)
    requested_book_mode = str(args.book_mode).strip().lower() if args.book_mode else str(cfg.book_mode).strip().lower()
    requested_universe_size = max(10, int(args.universe_size))

    if model == "profit_asset_gate":
        min_required = 400 if requested_book_mode in {"sector_weighted", "none_weighted"} else max(200, 4 * requested_top_n)
        if requested_universe_size < min_required:
            requested_universe_size = min_required
            print(f"adjusted_universe_size: {requested_universe_size}")
    elif model == "profit_asset_gate_proxy":
        min_required = 200 if requested_book_mode in {"sector_weighted", "none_weighted"} else max(50, 2 * requested_top_n)
        if requested_universe_size < min_required:
            requested_universe_size = min_required
            print(f"adjusted_universe_size: {requested_universe_size}")

    smoke_universe_file = cfg.tmp_dir / f"smoke_universe_{requested_universe_size}.csv"
    smoke_signal_file = cfg.tmp_dir / f"smoke_signal_{model}_{trade_date.isoformat()}.csv"
    universe_file = (
        Path(args.universe_file).resolve()
        if args.universe_file
        else (cfg.private_dir / "universe.csv")
    )
    fundamentals_file = (
        Path(args.fundamentals_file).resolve()
        if args.fundamentals_file
        else cfg.fundamentals_file
    )
    classifications_file = (
        Path(args.classifications_file).resolve()
        if args.classifications_file
        else cfg.classifications_file
    )

    if model == "profit_asset_gate":
        missing_paths = [
            str(path)
            for path in [fundamentals_file, classifications_file]
            if not Path(path).exists()
        ]
        if missing_paths:
            joined = ", ".join(missing_paths)
            raise RuntimeError(
                "profit_asset_gate smoke blocked: missing reference files: "
                f"{joined}"
            )

    _build_smoke_universe(
        universe_file=universe_file,
        signals_dir=cfg.signals_dir,
        output_file=smoke_universe_file,
        universe_size=requested_universe_size,
    )

    sg_args = SimpleNamespace(
        date=trade_date.isoformat(),
        signals_dir="",
        output_file=str(smoke_signal_file),
        universe_file=str(smoke_universe_file),
        sector_map_file="",
        fundamentals_file=str(fundamentals_file),
        classifications_file=str(classifications_file),
        strategy_file="",
        model=model,
        group_level=str(args.group_level or "auto"),
        book_mode=requested_book_mode,
        lookback_days=int(args.lookback_days),
        smoothing=int(args.smoothing),
        top_n=requested_top_n if args.top_n is not None else None,
        signal_decay=0,
        score_truncation="",
    )
    generated_path = run_signal_generation(sg_args)

    rb_args = SimpleNamespace(
        date=trade_date.isoformat(),
        signal_file=str(generated_path),
        signals_dir="",
        strategy_file="",
        db_path="",
        logs_dir="",
        top_n=requested_top_n if args.top_n is not None else None,
        gross_exposure=float(args.gross_exposure)
        if args.gross_exposure is not None
        else None,
        book_mode=str(args.book_mode).strip().lower() if args.book_mode else "",
        dry_run=True,
        enforce_et_window=False,
        et_target_time="",
        et_window_minutes=20,
    )
    exit_code = int(run_rebalance(rb_args))
    if exit_code != 0:
        raise RuntimeError(f"Pipeline smoke failed with exit code {exit_code}.")

    print("pipeline_smoke: ok")
    print(f"model: {model}")
    print(f"trade_date: {trade_date.isoformat()}")
    print(f"smoke_universe_file: {smoke_universe_file}")
    print(f"smoke_signal_file: {smoke_signal_file}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Connectivity smoke test and optional dry-run pipeline smoke."
    )
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="Run a dry-run end-to-end pipeline smoke on a sampled universe.",
    )
    parser.add_argument(
        "--date",
        default="",
        help="Trade date in YYYY-MM-DD (ET). Defaults to today in ET.",
    )
    parser.add_argument(
        "--universe-size",
        type=int,
        default=100,
        help="Number of symbols to sample into the smoke-test universe.",
    )
    parser.add_argument(
        "--universe-file",
        default="",
        help="Optional explicit universe CSV path.",
    )
    parser.add_argument(
        "--model",
        choices=MODEL_CHOICES,
        default=None,
        help="Signal model to smoke-test. Defaults to ALPACA_SIGNAL_MODEL from .env.",
    )
    parser.add_argument(
        "--group-level",
        choices=["industry", "sector", "auto", "market"],
        default="auto",
        help="Grouping level for proxy and research-model smoke runs.",
    )
    parser.add_argument(
        "--fundamentals-file",
        default="",
        help="Reference fundamentals CSV path for profit_asset_gate.",
    )
    parser.add_argument(
        "--classifications-file",
        default="",
        help="Reference classifications CSV path for profit_asset_gate.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=20,
        help="Daily bar lookback window for signal generation.",
    )
    parser.add_argument(
        "--smoothing",
        type=int,
        default=2,
        help="Smoothing window for failed_move_vwap smoke runs.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Override names per side for the dry-run rebalance smoke.",
    )
    parser.add_argument(
        "--gross-exposure",
        type=float,
        default=None,
        help="Override gross exposure for the dry-run rebalance smoke.",
    )
    parser.add_argument(
        "--book-mode",
        choices=["sector", "none", "sector_weighted", "none_weighted"],
        default="",
        help="Override portfolio construction mode for the smoke rebalance.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    run_connectivity_smoke()
    if args.pipeline:
        run_pipeline_smoke(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"smoke_test_error: {exc}", file=sys.stderr)
        raise SystemExit(1)
