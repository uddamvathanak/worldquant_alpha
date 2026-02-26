from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from math import sqrt
from pathlib import Path

import pandas as pd

from config import ET, load_config
from tracker import PaperTracker


def _month_bounds(month_str: str) -> tuple[date, date]:
    start = datetime.strptime(month_str, "%Y-%m").date().replace(day=1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    end = next_month - timedelta(days=1)
    return start, end


def compute_proxy_metrics(daily_metrics: pd.DataFrame) -> dict[str, float]:
    if daily_metrics.empty:
        return {
            "fitness_proxy": 0.0,
            "sharpe_proxy": 0.0,
            "margin_proxy_bps": 0.0,
            "returns": 0.0,
            "max_drawdown": 0.0,
            "turnover_mean": 0.0,
            "annual_return_proxy": 0.0,
        }

    frame = daily_metrics.sort_values("trade_date").reset_index(drop=True)
    returns = frame["daily_return"].astype(float)
    turnovers = frame["turnover"].astype(float)
    equities = frame["equity"].astype(float)

    ret_mean = float(returns.mean())
    annual_return_proxy = ret_mean * 252.0
    ret_std = float(returns.std(ddof=0))
    sharpe = float(sqrt(252.0) * ret_mean / ret_std) if ret_std > 0 else 0.0

    turnover_sum = float(turnovers.sum())
    margin = float(10_000.0 * float(returns.sum()) / turnover_sum) if turnover_sum > 0 else 0.0
    turnover_mean = float(turnovers.mean()) if len(turnovers) else 0.0
    fitness = float(sharpe * sqrt(abs(annual_return_proxy) / max(turnover_mean, 0.125)))

    cumulative = (1.0 + returns).cumprod()
    total_return = float(cumulative.iloc[-1] - 1.0) if len(cumulative) else 0.0

    peaks = equities.cummax()
    drawdowns = (peaks - equities) / peaks.replace(0, pd.NA)
    max_dd = float(drawdowns.fillna(0.0).max())

    return {
        "fitness_proxy": fitness,
        "sharpe_proxy": sharpe,
        "margin_proxy_bps": margin,
        "returns": total_return,
        "max_drawdown": max_dd,
        "turnover_mean": turnover_mean,
        "annual_return_proxy": annual_return_proxy,
    }


def _build_wqa_log_command(
    *,
    month: str,
    summary: dict[str, float],
    missed_run_count: int,
    expected_monthly_edge_usd: float,
    qc_candidate: bool,
) -> str:
    return (
        "wqa log-result "
        f"--title \"Alpaca Paper Monthly {month}\" "
        "--expression \"paper/alpaca/rebalance_runner.py\" "
        f"--dataset \"alpaca-paper-{month}\" "
        "--status watch "
        "--notes \"Monthly proxy metrics from Alpaca paper pipeline.\" "
        f"--returns {summary['returns']:.6f} "
        f"--turnover {summary['turnover_mean']:.6f} "
        f"--max-drawdown {summary['max_drawdown']:.6f} "
        f"--metric fitness_proxy={summary['fitness_proxy']:.6f} "
        f"--metric sharpe_proxy={summary['sharpe_proxy']:.6f} "
        f"--metric margin_proxy_bps={summary['margin_proxy_bps']:.6f} "
        f"--metric annual_return_proxy={summary['annual_return_proxy']:.6f} "
        f"--metric missed_run_count={missed_run_count} "
        f"--metric expected_monthly_edge_usd={expected_monthly_edge_usd:.2f} "
        f"--metric qc_candidate={int(qc_candidate)} "
        "--source-platform \"Alpaca Paper\""
    )


def run_monthly_eval(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.db_path:
        cfg.db_path = Path(args.db_path).resolve()
    if args.logs_dir:
        cfg.logs_dir = Path(args.logs_dir).resolve()
    cfg.ensure_runtime_dirs()

    month = args.month.strip() if args.month else datetime.now(ET).strftime("%Y-%m")
    start, end = _month_bounds(month)

    tracker = PaperTracker(cfg.db_path, cfg.logs_dir)
    daily = tracker.get_daily_metrics_between(start, end)
    if daily.empty:
        print(f"No daily metrics found for {month}.")
        return 1

    summary = compute_proxy_metrics(daily)
    missed_count = tracker.count_missed_runs_between(start, end)

    latest_equity = float(daily.sort_values("trade_date").iloc[-1]["equity"])
    trailing_mean_return = float(daily["daily_return"].astype(float).tail(20).mean())
    expected_monthly_edge_usd = latest_equity * trailing_mean_return * 21.0

    metrics_stable = (
        summary["fitness_proxy"] > 0
        and summary["sharpe_proxy"] > 0
        and summary["margin_proxy_bps"] > 0
        and summary["max_drawdown"] < 0.15
    )
    qc_candidate = bool(expected_monthly_edge_usd > 60.0 and metrics_stable)

    out_row = {
        "month": month,
        "fitness_proxy": summary["fitness_proxy"],
        "sharpe_proxy": summary["sharpe_proxy"],
        "margin_proxy_bps": summary["margin_proxy_bps"],
        "returns": summary["returns"],
        "max_drawdown": summary["max_drawdown"],
        "turnover_mean": summary["turnover_mean"],
        "annual_return_proxy": summary["annual_return_proxy"],
        "missed_run_count": missed_count,
        "expected_monthly_edge_usd": expected_monthly_edge_usd,
        "qc_candidate": int(qc_candidate),
    }
    out_path = cfg.logs_dir / f"summary_{month}.csv"
    pd.DataFrame([out_row]).to_csv(out_path, index=False)

    wqa_cmd = _build_wqa_log_command(
        month=month,
        summary=summary,
        missed_run_count=missed_count,
        expected_monthly_edge_usd=expected_monthly_edge_usd,
        qc_candidate=qc_candidate,
    )

    print(f"Monthly summary written: {out_path}")
    for key in [
        "fitness_proxy",
        "sharpe_proxy",
        "margin_proxy_bps",
        "returns",
        "max_drawdown",
        "turnover_mean",
    ]:
        print(f"{key}: {out_row[key]:.6f}")
    print(f"missed_run_count: {missed_count}")
    print(f"expected_monthly_edge_usd: {expected_monthly_edge_usd:.2f}")
    print(f"qc_candidate: {int(qc_candidate)}")
    print("wqa_log_command:")
    print(wqa_cmd)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monthly evaluator for Alpaca paper-trading proxy metrics."
    )
    parser.add_argument(
        "--month",
        default="",
        help="Month in YYYY-MM. Default: current ET month.",
    )
    parser.add_argument("--db-path", default="", help="Override SQLite path.")
    parser.add_argument("--logs-dir", default="", help="Override logs directory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(run_monthly_eval(args))


if __name__ == "__main__":
    raise SystemExit(main())

