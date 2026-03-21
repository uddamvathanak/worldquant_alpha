from __future__ import annotations

import argparse
from pathlib import Path

from alpha_registry import MODEL_RESEARCH_SELECTED, load_strategy_spec
from config import load_config
from config import et_now
from rebalance_runner import run_rebalance
from signal_generator import MODEL_CHOICES, run_signal_generation
from universe_builder import run_universe_build


def _minutes_of_day(hour: int, minute: int) -> int:
    return int(hour * 60 + minute)


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format: {value}. Expected HH:MM.")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"Invalid time value: {value}.")
    return hour, minute


def _is_within_et_window(target_hhmm: str, *, window_minutes: int) -> bool:
    now = et_now()
    target_hour, target_minute = _parse_hhmm(target_hhmm)
    now_m = _minutes_of_day(now.hour, now.minute)
    target_m = _minutes_of_day(target_hour, target_minute)
    direct = abs(now_m - target_m)
    wrapped = 1440 - direct
    delta = min(direct, wrapped)
    return bool(delta <= window_minutes)


def run_daily_pipeline(args: argparse.Namespace) -> int:
    cfg = load_config()
    strategy_file = (
        Path(args.strategy_file).resolve()
        if getattr(args, "strategy_file", "")
        else cfg.selected_strategy_file
    )
    if args.model is None and strategy_file.exists():
        try:
            strategy = load_strategy_spec(strategy_file, require_approved=True)
            args.model = MODEL_RESEARCH_SELECTED
            if args.book_mode == "":
                args.book_mode = strategy.book_mode
            if args.top_n is None:
                args.top_n = strategy.top_n
            if args.gross_exposure is None:
                args.gross_exposure = strategy.gross_exposure
            if args.group_level == "auto":
                args.group_level = strategy.group_level
        except Exception:
            pass

    signal_file = args.signal_file.strip()

    if args.enforce_et_window and not args.date:
        et_target = args.et_target_time.strip() or "09:35"
        et_window = max(1, int(args.et_window_minutes))
        if not _is_within_et_window(et_target, window_minutes=et_window):
            rb_args = argparse.Namespace(
                date=args.date,
                signal_file=signal_file,
                signals_dir=args.signals_dir,
                strategy_file=args.strategy_file,
                db_path=args.db_path,
                logs_dir=args.logs_dir,
                top_n=args.top_n,
                gross_exposure=args.gross_exposure,
                book_mode=args.book_mode,
                dry_run=args.dry_run,
                enforce_et_window=args.enforce_et_window,
                et_target_time=args.et_target_time,
                et_window_minutes=args.et_window_minutes,
            )
            return int(run_rebalance(rb_args))

    if not args.skip_signal_generation:
        if not args.skip_universe_refresh:
            ub_args = argparse.Namespace(
                date=args.date,
                output_file=args.universe_file,
                max_symbols=args.universe_max_symbols,
                lookback_days=args.universe_lookback_days,
                min_price=args.universe_min_price,
                min_dollar_volume=args.universe_min_dollar_volume,
                min_coverage=args.universe_min_coverage,
                shortable_only=(args.universe_shortable_policy == "shortable_only"),
                include_non_shortable=(args.universe_shortable_policy != "shortable_only"),
            )
            try:
                run_universe_build(ub_args)
            except Exception as exc:
                if args.strict_universe_refresh:
                    raise
                print(f"warning: universe_refresh_failed: {exc}")

        sg_args = argparse.Namespace(
            date=args.date,
            signals_dir=args.signals_dir,
            output_file=args.output_file,
            universe_file=args.universe_file,
            sector_map_file=args.sector_map_file,
            fundamentals_file=args.fundamentals_file,
            classifications_file=args.classifications_file,
            strategy_file=args.strategy_file,
            model=args.model,
            group_level=args.group_level,
            lookback_days=args.lookback_days,
            smoothing=args.smoothing,
            top_n=args.top_n,
            signal_decay=args.signal_decay,
            score_truncation=args.score_truncation,
        )
        generated_path = run_signal_generation(sg_args)
        signal_file = str(generated_path)
    elif not signal_file and args.output_file:
        signal_file = str(Path(args.output_file).resolve())

    rb_args = argparse.Namespace(
        date=args.date,
        signal_file=signal_file,
        signals_dir=args.signals_dir,
        strategy_file=args.strategy_file,
        db_path=args.db_path,
        logs_dir=args.logs_dir,
        top_n=args.top_n,
        gross_exposure=args.gross_exposure,
        book_mode=args.book_mode,
        dry_run=args.dry_run,
        enforce_et_window=args.enforce_et_window,
        et_target_time=args.et_target_time,
        et_window_minutes=args.et_window_minutes,
    )
    return int(run_rebalance(rb_args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate daily signal CSV from Alpaca data, then run rebalance pipeline."
        )
    )
    parser.add_argument("--date", default="", help="Trade date in YYYY-MM-DD (ET).")

    # Universe refresh knobs.
    parser.add_argument(
        "--skip-universe-refresh",
        action="store_true",
        help="Skip automatic universe refresh before signal generation.",
    )
    parser.add_argument(
        "--strict-universe-refresh",
        action="store_true",
        help="Fail fast if universe refresh fails (default is warn and continue).",
    )
    parser.add_argument(
        "--universe-max-symbols",
        type=int,
        default=3000,
        help="Maximum symbols kept by universe refresh ranking.",
    )
    parser.add_argument(
        "--universe-lookback-days",
        type=int,
        default=20,
        help="Lookback window for universe liquidity ranking.",
    )
    parser.add_argument(
        "--universe-min-price",
        type=float,
        default=3.0,
        help="Minimum average close for universe eligibility.",
    )
    parser.add_argument(
        "--universe-min-dollar-volume",
        type=float,
        default=0.0,
        help="Minimum average daily dollar volume for universe eligibility.",
    )
    parser.add_argument(
        "--universe-min-coverage",
        type=float,
        default=0.80,
        help="Minimum bar-coverage ratio in the universe lookback window.",
    )
    parser.add_argument(
        "--universe-shortable-policy",
        choices=["shortable_only", "all_tradable"],
        default="shortable_only",
        help="Universe membership policy for shortability.",
    )

    # Signal generation knobs.
    parser.add_argument(
        "--skip-signal-generation",
        action="store_true",
        help="Skip generation step and use existing signal file path.",
    )
    parser.add_argument("--signals-dir", default="", help="Override signals directory.")
    parser.add_argument(
        "--signal-file",
        default="",
        help="Existing signal CSV path when --skip-signal-generation is used.",
    )
    parser.add_argument(
        "--output-file",
        default="",
        help="Optional explicit output path for generated signals.",
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
        "--fundamentals-file",
        default="",
        help="Reference fundamentals CSV path for fundamental models.",
    )
    parser.add_argument(
        "--classifications-file",
        default="",
        help="Reference classifications CSV path for fundamental models.",
    )
    parser.add_argument(
        "--strategy-file",
        default="",
        help="Promoted selected strategy JSON path for daily runtime.",
    )
    parser.add_argument(
        "--model",
        choices=MODEL_CHOICES,
        default=None,
        help="Signal model to generate. Defaults to ALPACA_SIGNAL_MODEL from .env.",
    )
    parser.add_argument(
        "--group-level",
        choices=["industry", "sector", "auto", "market"],
        default="auto",
        help="Grouping level for proxy and research price/volume models.",
    )
    parser.add_argument(
        "--signal-decay",
        type=int,
        default=0,
        help="Linear score decay window for research models.",
    )
    parser.add_argument(
        "--score-truncation",
        default="",
        help="Optional absolute score contribution cap for research models (e.g. 0.05).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=60,
        help="Calendar lookback days for signal generation.",
    )
    parser.add_argument(
        "--smoothing",
        type=int,
        default=2,
        help="Rolling smoothing window for generated signal.",
    )

    # Rebalance knobs.
    parser.add_argument("--db-path", default="", help="Override SQLite path.")
    parser.add_argument("--logs-dir", default="", help="Override CSV logs directory.")
    parser.add_argument("--top-n", type=int, default=None, help="Names per side.")
    parser.add_argument(
        "--gross-exposure",
        type=float,
        default=None,
        help="Total gross exposure (e.g. 0.80).",
    )
    parser.add_argument(
        "--book-mode",
        choices=["sector", "none"],
        default="",
        help="Portfolio construction mode used by the rebalance stage.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build targets and logs without live order submission.",
    )
    parser.add_argument(
        "--enforce-et-window",
        action="store_true",
        help="Skip execution outside ET target window.",
    )
    parser.add_argument(
        "--et-target-time",
        default="",
        help="Target ET time in HH:MM for --enforce-et-window.",
    )
    parser.add_argument(
        "--et-window-minutes",
        type=int,
        default=20,
        help="Allowed ET minute distance from target when enforcing window.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(run_daily_pipeline(args))


if __name__ == "__main__":
    raise SystemExit(main())
