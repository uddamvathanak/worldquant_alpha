from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
from math import sqrt
from pathlib import Path
import uuid

import pandas as pd

from broker_alpaca import AlpacaBroker
from config import et_now, load_config, parse_trade_date
from execution import (
    apply_margin_guard,
    augment_targets_with_flat_positions,
    build_order_plan,
    estimate_traded_notional,
    estimate_turnover,
    execute_order_plan,
    extract_rejected_symbols,
    extract_rejected_short_symbols,
    merge_event_frames,
)
from portfolio_builder import (
    build_sector_neutral_targets,
    drop_rejected_shorts_and_reneutralize,
    portfolio_exposure,
)
from signal_loader import load_signal_file, signal_path_for_date
from tracker import DailyMetricRecord, PaperTracker


def _rolling_sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    series = pd.Series(returns, dtype="float64")
    std = float(series.std(ddof=0))
    if std <= 0:
        return 0.0
    return float(sqrt(252.0) * series.mean() / std)


def _rolling_margin_proxy_bps(returns: list[float], turnovers: list[float]) -> float:
    turnover_sum = float(pd.Series(turnovers, dtype="float64").sum())
    if turnover_sum <= 0:
        return 0.0
    ret_sum = float(pd.Series(returns, dtype="float64").sum())
    return float(10_000.0 * ret_sum / turnover_sum)


def _rolling_fitness_proxy(returns: list[float], turnovers: list[float], sharpe: float) -> float:
    if not returns:
        return 0.0
    ret_mean = float(pd.Series(returns, dtype="float64").mean())
    turnover_mean = float(pd.Series(turnovers, dtype="float64").mean())
    annual_return_proxy = ret_mean * 252.0
    denom = max(turnover_mean, 0.125)
    if denom <= 0:
        return 0.0
    return float(sharpe * sqrt(abs(annual_return_proxy) / denom))


def _max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    series = pd.Series(equity_curve, dtype="float64")
    peaks = series.cummax()
    drawdowns = (peaks - series) / peaks.replace(0, pd.NA)
    return float(drawdowns.fillna(0.0).max())


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


def _is_within_et_window(target_hhmm: str, *, window_minutes: int) -> tuple[bool, str]:
    now = et_now()
    target_hour, target_minute = _parse_hhmm(target_hhmm)
    now_m = _minutes_of_day(now.hour, now.minute)
    target_m = _minutes_of_day(target_hour, target_minute)
    direct = abs(now_m - target_m)
    wrapped = 1440 - direct
    delta = min(direct, wrapped)
    ok = delta <= window_minutes
    detail = (
        f"now_et={now.strftime('%Y-%m-%d %H:%M:%S %Z')} "
        f"target_et={target_hhmm} delta_min={delta} window_min={window_minutes}"
    )
    return ok, detail


def _detect_and_log_missed_runs(
    tracker: PaperTracker,
    broker: AlpacaBroker,
    trade_date: date,
    current_run_id: str,
) -> None:
    last_run_date = tracker.get_last_run_trade_date(exclude_run_id=current_run_id)
    if last_run_date is None:
        return
    if last_run_date >= trade_date - timedelta(days=1):
        return

    start = last_run_date + timedelta(days=1)
    end = trade_date - timedelta(days=1)
    try:
        missing_days = broker.list_trading_days(start, end)
    except Exception:
        missing_days = [dt.date() for dt in pd.bdate_range(start=start, end=end)]

    for missed_day in missing_days:
        tracker.log_missed_run(
            missed_day,
            reason="No scheduled execution detected before next observed run.",
        )


def _build_daily_metric_record(
    tracker: PaperTracker,
    *,
    trade_date: date,
    run_id: str,
    equity: float,
    traded_notional: float,
    round_trip_cost_bps: float,
) -> DailyMetricRecord:
    prev = tracker.get_latest_daily_metric_before(trade_date)
    prev_equity = float(prev["equity"]) if prev else float(equity)

    pnl_gross = float(equity - prev_equity)
    daily_return = float(pnl_gross / prev_equity) if prev_equity > 0 else 0.0
    turnover = estimate_turnover(traded_notional, prev_equity)
    cost_rate = round_trip_cost_bps / 10_000.0
    pnl_net = float(pnl_gross - cost_rate * traded_notional)

    hist = tracker.get_recent_daily_metrics(limit=19)
    hist_returns = hist["daily_return"].astype(float).tolist() if not hist.empty else []
    hist_turnovers = hist["turnover"].astype(float).tolist() if not hist.empty else []
    hist_equity = hist["equity"].astype(float).tolist() if not hist.empty else []
    rolling_returns = hist_returns + [daily_return]
    rolling_turnovers = hist_turnovers + [turnover]

    sharpe_20 = _rolling_sharpe(rolling_returns)
    margin_20 = _rolling_margin_proxy_bps(rolling_returns, rolling_turnovers)
    fitness_20 = _rolling_fitness_proxy(rolling_returns, rolling_turnovers, sharpe_20)
    max_dd_to_date = _max_drawdown(hist_equity + [equity])

    return DailyMetricRecord(
        trade_date=trade_date.isoformat(),
        run_id=run_id,
        equity=float(equity),
        prev_equity=float(prev_equity),
        daily_return=float(daily_return),
        traded_notional=float(traded_notional),
        turnover=float(turnover),
        pnl_gross=float(pnl_gross),
        pnl_net=float(pnl_net),
        cost_bps=float(round_trip_cost_bps),
        sharpe_20=float(sharpe_20),
        margin_proxy_bps_20=float(margin_20),
        fitness_proxy_20=float(fitness_20),
        max_drawdown_to_date=float(max_dd_to_date),
    )


def run_rebalance(args: argparse.Namespace) -> int:
    cfg = load_config()

    if args.top_n is not None:
        cfg.top_n = int(args.top_n)
    if args.gross_exposure is not None:
        cfg.gross_exposure = float(args.gross_exposure)
    if args.db_path:
        cfg.db_path = Path(args.db_path).resolve()
    if args.logs_dir:
        cfg.logs_dir = Path(args.logs_dir).resolve()
    if args.signals_dir:
        cfg.signals_dir = Path(args.signals_dir).resolve()
    cfg.ensure_runtime_dirs()

    trade_date = parse_trade_date(args.date)
    signal_path = (
        Path(args.signal_file).resolve()
        if args.signal_file
        else signal_path_for_date(cfg.signals_dir, trade_date)
    )
    run_id = uuid.uuid4().hex[:12]

    tracker = PaperTracker(cfg.db_path, cfg.logs_dir)
    tracker.log_run_start(
        run_id=run_id,
        trade_date=trade_date,
        signal_path=signal_path,
        config=cfg.to_public_dict(),
        status="started",
    )

    try:
        if args.enforce_et_window and not args.date:
            et_target = args.et_target_time.strip() or cfg.scheduler_time_et
            et_window = max(1, int(args.et_window_minutes))
            in_window, detail = _is_within_et_window(et_target, window_minutes=et_window)
            if not in_window:
                tracker.update_run_finish(
                    run_id,
                    status="skipped_schedule_window",
                    reason=detail,
                )
                print(f"[{trade_date}] skipped_schedule_window {detail}")
                return 0

        api_key, api_secret = cfg.require_alpaca_credentials()
        broker = AlpacaBroker(api_key, api_secret, paper=True)

        _detect_and_log_missed_runs(tracker, broker, trade_date, run_id)

        account_pre = broker.get_account_snapshot()
        positions_pre = broker.list_positions()
        tracker.log_account_snapshot(
            run_id=run_id,
            snapshot_stage="pre",
            account=account_pre,
        )
        tracker.log_position_snapshot(
            run_id=run_id,
            snapshot_stage="pre",
            positions=positions_pre,
        )

        if not signal_path.exists():
            record = _build_daily_metric_record(
                tracker,
                trade_date=trade_date,
                run_id=run_id,
                equity=float(account_pre.get("equity", 0.0)),
                traded_notional=0.0,
                round_trip_cost_bps=cfg.round_trip_cost_bps,
            )
            tracker.upsert_daily_metric(record)
            tracker.update_run_finish(
                run_id,
                status="skipped_no_signal",
                reason=f"Signal file missing: {signal_path}",
            )
            print(f"[{trade_date}] skipped_no_signal: {signal_path}")
            return 0

        prev_metric = tracker.get_latest_daily_metric_before(trade_date)
        if prev_metric and float(prev_metric["daily_return"]) <= cfg.kill_switch_daily_return:
            record = _build_daily_metric_record(
                tracker,
                trade_date=trade_date,
                run_id=run_id,
                equity=float(account_pre.get("equity", 0.0)),
                traded_notional=0.0,
                round_trip_cost_bps=cfg.round_trip_cost_bps,
            )
            tracker.upsert_daily_metric(record)
            tracker.update_run_finish(
                run_id,
                status="skipped_kill_switch",
                reason=(
                    "Kill switch triggered: prior daily_return="
                    f"{float(prev_metric['daily_return']):.6f} <= "
                    f"{cfg.kill_switch_daily_return:.6f}"
                ),
            )
            print(f"[{trade_date}] skipped_kill_switch")
            return 0

        signals = load_signal_file(signal_path, trade_date=trade_date)
        short_candidates = signals.nsmallest(cfg.top_n, "score")["symbol"].tolist()
        shortable_map = broker.get_shortable_map(short_candidates)

        build = build_sector_neutral_targets(
            signals,
            equity=float(account_pre.get("equity", 0.0)),
            top_n=cfg.top_n,
            gross_exposure=cfg.gross_exposure,
            shortable_map=shortable_map,
        )
        final_core_targets = build.targets.copy()
        final_effective_targets = pd.DataFrame()
        events_all: list[pd.DataFrame] = []
        retry_summaries: list[dict[str, object]] = []
        margin_guard_passes: list[dict[str, object]] = []
        all_rejected_shorts: set[str] = set()
        unresolved_rejected_symbols: list[str] = []
        max_passes = max(1, int(cfg.max_retry_passes))

        positions_for_pass = positions_pre.copy()
        account_for_pass = dict(account_pre)
        for pass_num in range(1, max_passes + 1):
            final_effective_targets = augment_targets_with_flat_positions(
                final_core_targets,
                positions_for_pass,
            )
            target_stage = "initial" if pass_num == 1 else f"retry_{pass_num}"
            tracker.log_targets(
                run_id=run_id,
                targets=final_effective_targets,
                target_stage=target_stage,
            )

            if final_effective_targets.empty:
                retry_summaries.append(
                    {
                        "pass_num": pass_num,
                        "result": "no_targets",
                        "rejected_symbols": [],
                        "rejected_shorts": [],
                    }
                )
                break

            symbols_for_prices = final_effective_targets["symbol"].tolist()
            price_map = broker.get_latest_price_map(symbols_for_prices)
            order_plan = build_order_plan(
                final_effective_targets,
                positions_for_pass,
                min_order_notional=cfg.min_order_notional,
                price_map=price_map,
            )
            order_plan, margin_guard = apply_margin_guard(
                order_plan,
                buying_power=float(account_for_pass.get("buying_power", 0.0)),
                bp_utilization=cfg.bp_utilization,
                margin_buffer_notional=cfg.margin_buffer_notional,
                min_order_notional=cfg.min_order_notional,
                price_map=price_map,
            )
            margin_guard_passes.append(
                {
                    "pass_num": pass_num,
                    "orders_after_guard": int(len(order_plan)),
                    **margin_guard,
                }
            )

            if order_plan.empty:
                retry_summaries.append(
                    {
                        "pass_num": pass_num,
                        "result": "no_orders",
                        "rejected_symbols": [],
                        "rejected_shorts": [],
                    }
                )
                break

            events_pass = execute_order_plan(
                broker,
                order_plan,
                run_id=run_id,
                pass_num=pass_num,
                dry_run=args.dry_run,
            )
            events_all.append(events_pass)

            rejected_symbols = extract_rejected_symbols(events_pass)
            rejected_shorts = extract_rejected_short_symbols(events_pass)
            all_rejected_shorts.update(rejected_shorts)
            retry_summaries.append(
                {
                    "pass_num": pass_num,
                    "result": "ok" if not rejected_symbols else "retries_needed",
                    "submitted_orders": int(len(events_pass)),
                    "rejected_symbols": rejected_symbols,
                    "rejected_shorts": rejected_shorts,
                }
            )

            if not rejected_symbols:
                break

            if pass_num >= max_passes:
                unresolved_rejected_symbols = rejected_symbols
                break

            if rejected_shorts:
                final_core_targets = drop_rejected_shorts_and_reneutralize(
                    final_core_targets,
                    rejected_shorts,
                    short_gross_target=cfg.short_gross_target,
                )

            if not args.dry_run:
                positions_for_pass = broker.list_positions()
                account_for_pass = broker.get_account_snapshot()

        events = merge_event_frames(events_all)
        tracker.log_order_events(events)

        account_post = broker.get_account_snapshot()
        positions_post = broker.list_positions()
        tracker.log_account_snapshot(
            run_id=run_id,
            snapshot_stage="post",
            account=account_post,
        )
        tracker.log_position_snapshot(
            run_id=run_id,
            snapshot_stage="post",
            positions=positions_post,
        )

        traded_notional = 0.0 if args.dry_run else estimate_traded_notional(events)
        metric_record = _build_daily_metric_record(
            tracker,
            trade_date=trade_date,
            run_id=run_id,
            equity=float(account_post.get("equity", 0.0)),
            traded_notional=traded_notional,
            round_trip_cost_bps=cfg.round_trip_cost_bps,
        )
        tracker.upsert_daily_metric(metric_record)

        account_export = pd.DataFrame(
            [
                {"snapshot_stage": "pre", **account_pre},
                {"snapshot_stage": "post", **account_post},
            ]
        )
        pre_export = positions_pre.copy()
        pre_export["snapshot_stage"] = "pre"
        post_export = positions_post.copy()
        post_export["snapshot_stage"] = "post"
        positions_export = pd.concat([pre_export, post_export], ignore_index=True)
        targets_export = final_effective_targets.drop(columns=["force_order"], errors="ignore")
        tracker.export_daily_csvs(
            trade_date=trade_date,
            account=account_export,
            positions=positions_export,
            targets=targets_export,
            orders=events,
        )

        core_exposure = (
            portfolio_exposure(final_core_targets) if not final_core_targets.empty else {}
        )
        effective_exposure = (
            portfolio_exposure(final_effective_targets)
            if not final_effective_targets.empty
            else {}
        )
        flatten_symbols = (
            final_effective_targets.loc[
                final_effective_targets["side"] == "flat",
                "symbol",
            ]
            .astype(str)
            .str.upper()
            .unique()
            .tolist()
            if not final_effective_targets.empty
            else []
        )
        flatten_symbols = sorted(flatten_symbols)
        status = "dry_run_success" if args.dry_run else "success"
        if (not args.dry_run) and unresolved_rejected_symbols:
            status = "success_with_rejects"
        margin_guard_pass1 = margin_guard_passes[0] if len(margin_guard_passes) >= 1 else None
        margin_guard_pass2 = margin_guard_passes[1] if len(margin_guard_passes) >= 2 else None
        reason_payload = {
            "stats": build.stats,
            "filtered_short_symbols": build.filtered_short_symbols,
            "rejected_shorts": sorted(all_rejected_shorts),
            "unresolved_rejected_symbols": unresolved_rejected_symbols,
            "max_retry_passes": max_passes,
            "retry_passes": retry_summaries,
            "flatten_symbols_count": len(flatten_symbols),
            "flatten_symbols_sample": flatten_symbols[:20],
            "core_target_exposure": core_exposure,
            "effective_target_exposure": effective_exposure,
            "target_exposure": effective_exposure,
            "margin_guard_passes": margin_guard_passes,
            "margin_guard_pass1": margin_guard_pass1,
            "margin_guard_pass2": margin_guard_pass2,
            "traded_notional": traded_notional,
        }
        tracker.update_run_finish(
            run_id,
            status=status,
            reason=json.dumps(reason_payload, ensure_ascii=True),
        )
        print(f"[{trade_date}] {status} run_id={run_id}")
        return 0
    except Exception as exc:
        tracker.update_run_finish(
            run_id,
            status="error",
            reason=str(exc),
        )
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Daily Alpaca paper rebalance runner (sector-neutral long/short)."
    )
    parser.add_argument("--date", default="", help="Trade date in YYYY-MM-DD (ET).")
    parser.add_argument("--signal-file", default="", help="Override signal CSV path.")
    parser.add_argument("--signals-dir", default="", help="Override signals directory.")
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
        "--dry-run",
        action="store_true",
        help="Build targets and logs without live order submission.",
    )
    parser.add_argument(
        "--enforce-et-window",
        action="store_true",
        help="Skip execution when current ET time is outside --et-target-time +/- --et-window-minutes.",
    )
    parser.add_argument(
        "--et-target-time",
        default="",
        help="Target ET time in HH:MM when --enforce-et-window is enabled (default from config).",
    )
    parser.add_argument(
        "--et-window-minutes",
        type=int,
        default=20,
        help="Allowed ET minute distance from target time when --enforce-et-window is enabled.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(run_rebalance(args))


if __name__ == "__main__":
    raise SystemExit(main())
