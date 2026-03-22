from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import pandas as pd

from alpha_registry import (
    StrategyMember,
    StrategySpec,
    get_alpha_definition,
    resolve_alpha_set,
    write_strategy_spec,
)
from alpha_templates import compute_alpha_score_panel
from classification_store import (
    ClassificationStoreError,
    load_classifications_snapshot,
    load_symbol_master,
    resolve_classification_snapshot_path,
)
from historical_store import HistoricalStore
from monthly_eval import compute_proxy_metrics
from portfolio_builder import build_sector_neutral_targets
from signal_generator import (
    SignalGenerationError,
    compute_profit_asset_gate_proxy_panel,
    score_profit_asset_gate_proxy_frame,
)


DEFAULT_TRAIN_DAYS = 1008
DEFAULT_OOS_DAYS = 252
DEFAULT_TEST_DAYS = 252
DEFAULT_INITIAL_EQUITY = 100_000.0
WEIGHTED_BOOK_MODES = {"sector_weighted", "none_weighted"}


class BacktestError(RuntimeError):
    pass


@dataclass(slots=True)
class BacktestCandidate:
    profit_window: int
    asset_window: int
    mom_window: int
    group_level: str
    book_mode: str = "sector"
    top_n: int = 30
    gross_exposure: float = 4.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def name(self) -> str:
        return (
            f"pw{self.profit_window}_aw{self.asset_window}_mw{self.mom_window}_"
            f"{self.group_level}_{self.book_mode}"
        )


@dataclass(slots=True)
class ResearchCandidate:
    alpha_name: str
    family: str
    params: dict[str, Any]
    group_level: str
    book_mode: str
    top_n: int
    gross_exposure: float
    signal_decay: int = 0
    score_truncation: float | None = None

    @property
    def name(self) -> str:
        params_key = "_".join(
            f"{key}{str(value).replace('.', 'p')}"
            for key, value in sorted(self.params.items())
        )
        trunc_key = "none" if self.score_truncation is None else str(self.score_truncation).replace(".", "p")
        parts = [
            self.alpha_name,
            params_key or "default",
            self.group_level,
            self.book_mode,
            f"top{self.top_n}",
            f"decay{self.signal_decay}",
            f"trunc{trunc_key}",
        ]
        return "__".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha_name": self.alpha_name,
            "family": self.family,
            "params": dict(self.params),
            "group_level": self.group_level,
            "book_mode": self.book_mode,
            "top_n": self.top_n,
            "gross_exposure": self.gross_exposure,
            "signal_decay": self.signal_decay,
            "score_truncation": self.score_truncation,
            "candidate_name": self.name,
        }


@dataclass(slots=True)
class SplitWindows:
    latest_completed_date: date
    usable_end_date: date
    train_dates: list[date]
    oos_dates: list[date]
    test_dates: list[date]

    @property
    def execution_dates(self) -> list[date]:
        return self.train_dates + self.oos_dates + self.test_dates

    def split_for_date(self, execution_date: date) -> str:
        if execution_date in set(self.train_dates):
            return "train"
        if execution_date in set(self.oos_dates):
            return "oos"
        return "test"


def build_split_windows(
    trading_days: list[date],
    *,
    end_date: date,
    train_days: int = DEFAULT_TRAIN_DAYS,
    oos_days: int = DEFAULT_OOS_DAYS,
    test_days: int = DEFAULT_TEST_DAYS,
) -> SplitWindows:
    eligible = sorted(day for day in trading_days if day <= end_date)
    if len(eligible) < 2:
        raise BacktestError("Need at least two trading days to construct execution windows.")

    usable_execution_dates = eligible[:-1]
    required = int(train_days) + int(oos_days) + int(test_days)
    if len(usable_execution_dates) < required:
        raise BacktestError(
            f"Not enough trading days for requested split lengths: "
            f"have {len(usable_execution_dates)}, need {required}."
        )

    tail = usable_execution_dates[-required:]
    train = tail[:train_days]
    oos = tail[train_days : train_days + oos_days]
    test = tail[train_days + oos_days :]
    return SplitWindows(
        latest_completed_date=eligible[-1],
        usable_end_date=usable_execution_dates[-1],
        train_dates=train,
        oos_dates=oos,
        test_dates=test,
    )


def build_candidate_grid() -> list[BacktestCandidate]:
    out: list[BacktestCandidate] = []
    for profit_window in [42, 63, 84]:
        for asset_window in [42, 63, 84]:
            for mom_window in [3, 5, 10]:
                for group_level in ["industry", "sector"]:
                    out.append(
                        BacktestCandidate(
                            profit_window=profit_window,
                            asset_window=asset_window,
                            mom_window=mom_window,
                            group_level=group_level,
                        )
                    )
    return out


def _build_execution_calendar_maps(execution_dates: list[date], trading_days: list[date]) -> pd.DataFrame:
    day_to_index = {day: idx for idx, day in enumerate(trading_days)}
    rows: list[dict[str, object]] = []
    for execution_date in execution_dates:
        idx = day_to_index.get(execution_date)
        if idx is None or idx <= 0 or idx >= len(trading_days) - 1:
            continue
        rows.append(
            {
                "execution_date": execution_date,
                "signal_date": trading_days[idx - 1],
                "next_execution_date": trading_days[idx + 1],
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        raise BacktestError("Execution calendar map is empty after delay-1 alignment.")
    return out


def canonicalize_bars(
    bars: pd.DataFrame,
    symbol_master: pd.DataFrame,
) -> pd.DataFrame:
    if bars.empty:
        return bars.copy()

    master = symbol_master.copy()
    if master.empty:
        out = bars.copy()
        out["original_symbol"] = out["symbol"]
        return out

    master["symbol"] = master["symbol"].astype(str).str.strip().str.upper()
    master["canonical_symbol"] = (
        master["canonical_symbol"].astype(str).str.strip().str.upper()
    )
    lookup = master[["symbol", "canonical_symbol"]].drop_duplicates()

    out = bars.copy()
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out = out.merge(lookup, on="symbol", how="left")
    out["original_symbol"] = out["symbol"]
    out["symbol"] = out["canonical_symbol"].fillna(out["symbol"])
    aggregated = (
        out.sort_values(["trade_date", "symbol", "original_symbol"])
        .groupby(["symbol", "trade_date"], as_index=False)
        .agg(
            o=("o", "first"),
            h=("h", "max"),
            l=("l", "min"),
            c=("c", "last"),
            v=("v", "sum"),
            vw=("vw", "last"),
            n=("n", "sum"),
        )
    )
    return aggregated.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def build_universe_lookup(
    bars: pd.DataFrame,
    symbol_master: pd.DataFrame,
    *,
    signal_dates: list[date],
    lookback_days: int = 20,
    max_symbols: int = 3000,
    min_price: float = 3.0,
    min_coverage: float = 0.80,
) -> dict[date, pd.DataFrame]:
    if bars.empty:
        raise BacktestError("Cannot build historical universe from empty bars.")

    data = bars.sort_values(["symbol", "trade_date"]).reset_index(drop=True).copy()
    data["dollar_volume"] = data["c"].astype(float) * data["v"].astype(float)
    grouped = data.groupby("symbol")
    data["obs_count"] = grouped["c"].transform(
        lambda series: series.rolling(window=lookback_days, min_periods=1).count()
    )
    data["avg_close"] = grouped["c"].transform(
        lambda series: series.rolling(window=lookback_days, min_periods=1).mean()
    )
    data["avg_dollar_volume"] = grouped["dollar_volume"].transform(
        lambda series: series.rolling(window=lookback_days, min_periods=1).mean()
    )
    data["coverage"] = data["obs_count"].astype(float) / float(lookback_days)

    master = symbol_master.copy()
    if not master.empty:
        master["canonical_symbol"] = (
            master["canonical_symbol"].astype(str).str.strip().str.upper()
        )
        master["delisted_date"] = pd.to_datetime(
            master["delisted_date"], errors="coerce"
        ).dt.date
        master = (
            master.groupby("canonical_symbol", as_index=False)
            .agg(
                delisted_date=("delisted_date", "max"),
                is_delisted=("is_delisted", "max"),
            )
            .rename(columns={"canonical_symbol": "symbol"})
        )
        data = data.merge(master, on="symbol", how="left")
        active_mask = data["delisted_date"].isna() | (data["trade_date"] <= data["delisted_date"])
        data = data[active_mask].copy()
    else:
        data["delisted_date"] = pd.NaT
        data["is_delisted"] = False

    filtered = data[
        (data["avg_close"] >= float(min_price))
        & (data["coverage"] >= float(min_coverage))
    ].copy()
    lookup: dict[date, pd.DataFrame] = {}
    for signal_date, frame in filtered.groupby("trade_date"):
        if signal_date not in set(signal_dates):
            continue
        selected = frame.sort_values(
            ["avg_dollar_volume", "coverage", "symbol"],
            ascending=[False, False, True],
        ).head(int(max_symbols))
        lookup[signal_date] = selected[
            ["symbol", "avg_close", "avg_dollar_volume", "coverage", "obs_count"]
        ].reset_index(drop=True)
    return lookup


def _build_open_return_frame(bars: pd.DataFrame) -> pd.DataFrame:
    data = bars.sort_values(["symbol", "trade_date"]).reset_index(drop=True).copy()
    grouped = data.groupby("symbol")
    next_open = grouped["o"].shift(-1)
    data["period_return"] = next_open.div(data["o"]).sub(1.0)
    fallback = data["c"].div(data["o"]).sub(1.0)
    data["period_return"] = data["period_return"].where(data["period_return"].notna(), fallback)
    return data[["symbol", "trade_date", "period_return"]].copy()


def _select_best_candidate(leaderboard: pd.DataFrame) -> BacktestCandidate:
    if leaderboard.empty:
        raise BacktestError("Candidate leaderboard is empty.")
    ranked = leaderboard.sort_values(
        [
            "fitness_proxy",
            "sharpe_proxy",
            "turnover_mean",
            "window_sum",
        ],
        ascending=[False, False, True, True],
    ).reset_index(drop=True)
    best = ranked.iloc[0]
    return BacktestCandidate(
        profit_window=int(best["profit_window"]),
        asset_window=int(best["asset_window"]),
        mom_window=int(best["mom_window"]),
        group_level=str(best["group_level"]),
        book_mode=str(best["book_mode"]),
        top_n=int(best["top_n"]),
        gross_exposure=float(best["gross_exposure"]),
    )


def _align_weight_series(weights: pd.DataFrame) -> pd.Series:
    if weights.empty:
        return pd.Series(dtype="float64")
    series = (
        weights.groupby("symbol", as_index=True)["target_weight"]
        .sum()
        .astype(float)
        .sort_index()
    )
    return series


def _period_turnover(prev_weights: pd.Series, next_weights: pd.Series) -> float:
    symbols = sorted(set(prev_weights.index.tolist()) | set(next_weights.index.tolist()))
    if not symbols:
        return 0.0
    prev_aligned = prev_weights.reindex(symbols).fillna(0.0)
    next_aligned = next_weights.reindex(symbols).fillna(0.0)
    return float((next_aligned - prev_aligned).abs().sum())


def _min_scored_requirement(*, top_n: int, book_mode: str) -> int:
    if str(book_mode).strip().lower() in WEIGHTED_BOOK_MODES:
        return 200
    return max(50, 2 * int(top_n))


def _is_full_book(*, long_count: int, short_count: int, top_n: int, book_mode: str) -> bool:
    book_mode_norm = str(book_mode).strip().lower()
    if book_mode_norm in WEIGHTED_BOOK_MODES:
        threshold = max(100, min(int(top_n), 500))
        return bool(long_count >= threshold and short_count >= threshold)
    return bool(long_count >= int(top_n) and short_count >= int(top_n))


def run_backtest_candidate(
    bars: pd.DataFrame,
    classifications: pd.DataFrame,
    universe_lookup: dict[date, pd.DataFrame],
    execution_map: pd.DataFrame,
    candidate: BacktestCandidate,
    *,
    round_trip_cost_bps: float,
    initial_equity: float = DEFAULT_INITIAL_EQUITY,
    min_scored_symbols: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    min_scored = int(min_scored_symbols or _min_scored_requirement(top_n=candidate.top_n, book_mode=candidate.book_mode))
    panel = compute_profit_asset_gate_proxy_panel(
        bars,
        classifications=classifications,
        sector_map=None,
        group_level=candidate.group_level,
        profit_window=candidate.profit_window,
        asset_window=candidate.asset_window,
        mom_window=candidate.mom_window,
    )
    open_returns = _build_open_return_frame(bars)

    equity = float(initial_equity)
    previous_weights = pd.Series(dtype="float64")
    daily_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    position_rows: list[dict[str, Any]] = []

    for _, schedule_row in execution_map.iterrows():
        execution_date = schedule_row["execution_date"]
        signal_date = schedule_row["signal_date"]
        next_execution_date = schedule_row["next_execution_date"]
        universe = universe_lookup.get(signal_date)
        if universe is None or universe.empty:
            raise BacktestError(f"No historical universe available for signal date {signal_date}.")

        signal_slice = panel[
            (panel["trade_date"] == signal_date)
            & (panel["symbol"].isin(universe["symbol"]))
        ].copy()
        scores, diagnostics = score_profit_asset_gate_proxy_frame(
            signal_slice,
            min_scored_symbols=min_scored,
        )
        build = build_sector_neutral_targets(
            scores,
            equity=equity,
            top_n=candidate.top_n,
            gross_exposure=candidate.gross_exposure,
            book_mode=candidate.book_mode,
            shortable_map=None,
        )
        targets = build.targets.copy()
        current_weights = _align_weight_series(targets)
        turnover = _period_turnover(previous_weights, current_weights)
        cost_rate = float(round_trip_cost_bps) / 10_000.0
        cost_return = turnover * cost_rate

        period_slice = open_returns[
            (open_returns["trade_date"] == execution_date)
            & (open_returns["symbol"].isin(current_weights.index))
        ].copy()
        merged_returns = (
            period_slice.merge(
                targets[["symbol", "target_weight", "sector", "score"]],
                on="symbol",
                how="right",
            )
            .fillna({"period_return": 0.0, "target_weight": 0.0})
            .reset_index(drop=True)
        )
        gross_return = float(
            (merged_returns["target_weight"].astype(float) * merged_returns["period_return"].astype(float)).sum()
        )
        net_return = gross_return - cost_return
        next_equity = float(equity * (1.0 + net_return))

        daily_rows.append(
            {
                "execution_date": execution_date.isoformat(),
                "signal_date": signal_date.isoformat(),
                "next_execution_date": next_execution_date.isoformat(),
                "daily_return": net_return,
                "daily_return_gross": gross_return,
                "turnover": turnover,
                "cost_return": cost_return,
                "equity": next_equity,
                "equity_start": equity,
                "equity_end": next_equity,
                "candidate_name": candidate.name,
                "profit_window": candidate.profit_window,
                "asset_window": candidate.asset_window,
                "mom_window": candidate.mom_window,
                "group_level": candidate.group_level,
                "book_mode": candidate.book_mode,
            }
        )
        if not targets.empty:
            target_export = targets.copy()
            target_export["execution_date"] = execution_date.isoformat()
            target_export["signal_date"] = signal_date.isoformat()
            target_export["candidate_name"] = candidate.name
            target_rows.extend(target_export.to_dict("records"))

            position_export = targets.copy()
            position_export["execution_date"] = execution_date.isoformat()
            position_export["next_execution_date"] = next_execution_date.isoformat()
            position_export["candidate_name"] = candidate.name
            position_rows.extend(position_export.to_dict("records"))

        previous_weights = current_weights
        equity = next_equity

    daily_frame = pd.DataFrame(daily_rows)
    if daily_frame.empty:
        raise BacktestError("Backtest candidate produced no daily rows.")
    return (
        daily_frame,
        pd.DataFrame(target_rows),
        pd.DataFrame(position_rows),
    )


def summarize_candidate(
    daily_frame: pd.DataFrame,
    candidate: BacktestCandidate,
) -> dict[str, Any]:
    summary = compute_proxy_metrics(daily_frame.rename(columns={"execution_date": "trade_date"}))
    return {
        **candidate.to_dict(),
        **summary,
        "window_sum": candidate.profit_window + candidate.asset_window + candidate.mom_window,
    }


def run_parameter_sweep(
    bars: pd.DataFrame,
    classifications: pd.DataFrame,
    universe_lookup: dict[date, pd.DataFrame],
    execution_map: pd.DataFrame,
    *,
    round_trip_cost_bps: float,
    grid: list[BacktestCandidate] | None = None,
) -> pd.DataFrame:
    candidates = grid or build_candidate_grid()
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        daily_frame, _, _ = run_backtest_candidate(
            bars,
            classifications,
            universe_lookup,
            execution_map,
            candidate,
            round_trip_cost_bps=round_trip_cost_bps,
        )
        rows.append(summarize_candidate(daily_frame, candidate))
    return pd.DataFrame(rows).sort_values(
        ["fitness_proxy", "sharpe_proxy"],
        ascending=[False, False],
    ).reset_index(drop=True)


def run_full_backtest(
    *,
    cfg: Any,
    broker: object,
    end_date: date,
    feed: str,
    train_days: int,
    oos_days: int,
    test_days: int,
) -> dict[str, Any]:
    try:
        classification_snapshot_path = resolve_classification_snapshot_path(
            cfg.reference_dir,
            snapshot_date=end_date,
        )
        classifications = load_classifications_snapshot(
            cfg.reference_dir,
            snapshot_date=end_date,
        )
        symbol_master = load_symbol_master(cfg.reference_dir)
    except ClassificationStoreError as exc:
        raise BacktestError(str(exc)) from exc

    approx_calendar_start = end_date - timedelta(days=3650)
    store = HistoricalStore(cfg.cache_dir)
    trading_days = store.load_trading_calendar(
        start=approx_calendar_start,
        end=end_date,
        broker=broker,
    )
    splits = build_split_windows(
        trading_days,
        end_date=end_date,
        train_days=train_days,
        oos_days=oos_days,
        test_days=test_days,
    )
    execution_map = _build_execution_calendar_maps(splits.execution_dates, trading_days)
    earliest_signal = execution_map["signal_date"].min()
    if pd.isna(earliest_signal):
        raise BacktestError("Unable to determine earliest signal date for backtest.")
    fetch_start = pd.Timestamp(earliest_signal).date() - timedelta(days=240)

    candidate_symbols = (
        sorted(set(symbol_master["symbol"].astype(str).str.upper().tolist()))
        if not symbol_master.empty
        else sorted(set(classifications["symbol"].astype(str).str.upper().tolist()))
    )
    if not candidate_symbols:
        raise BacktestError("Classification cache produced no candidate symbols.")

    raw_bars = store.load_bars(
        candidate_symbols,
        start=fetch_start,
        end=splits.latest_completed_date,
        broker=broker,
        feed=feed,
        adjustment="split,spin-off",
    )
    canonical_bars = canonicalize_bars(raw_bars, symbol_master)
    signal_dates = execution_map["signal_date"].drop_duplicates().tolist()
    universe_lookup = build_universe_lookup(
        canonical_bars,
        symbol_master,
        signal_dates=signal_dates,
    )

    train_execution_map = execution_map[
        execution_map["execution_date"].isin(splits.train_dates)
    ].reset_index(drop=True)
    leaderboard = run_parameter_sweep(
        canonical_bars,
        classifications,
        universe_lookup,
        train_execution_map,
        round_trip_cost_bps=cfg.round_trip_cost_bps,
    )
    selected = _select_best_candidate(leaderboard)

    full_daily, full_targets, full_positions = run_backtest_candidate(
        canonical_bars,
        classifications,
        universe_lookup,
        execution_map,
        selected,
        round_trip_cost_bps=cfg.round_trip_cost_bps,
    )
    split_map = {
        execution_date.isoformat(): name
        for name, dates in [
            ("train", splits.train_dates),
            ("oos", splits.oos_dates),
            ("test", splits.test_dates),
        ]
        for execution_date in dates
    }
    full_daily["split"] = full_daily["execution_date"].map(split_map)
    if not full_targets.empty:
        full_targets["split"] = full_targets["execution_date"].map(split_map)
    if not full_positions.empty:
        full_positions["split"] = full_positions["execution_date"].map(split_map)

    split_rows: list[dict[str, Any]] = []
    for split_name in ["train", "oos", "test"]:
        subset = full_daily[full_daily["split"] == split_name].copy()
        metrics = compute_proxy_metrics(subset.rename(columns={"execution_date": "trade_date"}))
        split_rows.append({"split": split_name, **metrics})
    split_metrics = pd.DataFrame(split_rows)

    return {
        "selected_config": selected.to_dict(),
        "candidate_leaderboard": leaderboard,
        "split_metrics": split_metrics,
        "daily_equity": full_daily,
        "daily_targets": full_targets,
        "daily_positions": full_positions,
        "metadata": {
            "feed": feed,
            "latest_completed_date": splits.latest_completed_date.isoformat(),
            "usable_end_date": splits.usable_end_date.isoformat(),
            "classification_snapshot": str(classification_snapshot_path),
            "classification_source": cfg.classification_source,
            "train_days": train_days,
            "oos_days": oos_days,
            "test_days": test_days,
            "round_trip_cost_bps": cfg.round_trip_cost_bps,
            "gross_exposure": selected.gross_exposure,
            "book_mode": selected.book_mode,
            "approximations": [
                "FMP classifications are snapshot-based, not point-in-time historical classifications.",
                "Historical shortability is approximated from the long/short universe filter.",
                "Execution is replayed with next-day open to next-day open daily returns.",
            ],
        },
    }


def write_backtest_outputs(
    outputs: dict[str, Any],
    *,
    backtests_dir: Path,
    run_stamp: str,
) -> Path:
    run_dir = backtests_dir / run_stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "selected_config.json").write_text(
        json.dumps(outputs["selected_config"], indent=2),
        encoding="utf-8",
    )
    outputs["candidate_leaderboard"].to_csv(run_dir / "candidate_leaderboard.csv", index=False)
    outputs["split_metrics"].to_csv(run_dir / "split_metrics.csv", index=False)
    outputs["daily_equity"].to_csv(run_dir / "daily_equity.csv", index=False)
    outputs["daily_positions"].to_csv(run_dir / "daily_positions.csv", index=False)
    outputs["daily_targets"].to_csv(run_dir / "daily_targets.csv", index=False)
    (run_dir / "metadata.json").write_text(
        json.dumps(outputs["metadata"], indent=2),
        encoding="utf-8",
    )
    return run_dir


def expand_research_candidates(
    *,
    alpha_set: str,
    group_level_grid: list[str],
    book_mode_grid: list[str],
    top_n_grid: list[int],
    decay_grid: list[int],
    truncation_grid: list[float | None],
    gross_exposure: float,
) -> list[ResearchCandidate]:
    candidates: list[ResearchCandidate] = []
    for definition in resolve_alpha_set(alpha_set):
        grid_keys = sorted(definition.parameter_grid)
        param_values = [definition.parameter_grid[key] for key in grid_keys]
        combinations = [dict(definition.default_params)]
        if grid_keys:
            combinations = []
            for combo in pd.MultiIndex.from_product(param_values, names=grid_keys):
                params = dict(definition.default_params)
                params.update(dict(zip(grid_keys, combo)))
                combinations.append(params)
        for params in combinations:
            for group_level in group_level_grid:
                if not definition.supports_group_level and group_level != "market":
                    continue
                for book_mode in book_mode_grid:
                    for top_n in top_n_grid:
                        for signal_decay in decay_grid:
                            for truncation in truncation_grid:
                                candidates.append(
                                    ResearchCandidate(
                                        alpha_name=definition.name,
                                        family=definition.family,
                                        params=params,
                                        group_level=group_level,
                                        book_mode=book_mode,
                                        top_n=int(top_n),
                                        gross_exposure=float(gross_exposure),
                                        signal_decay=int(signal_decay),
                                        score_truncation=truncation,
                                    )
                                )
    return candidates


def run_research_candidate(
    bars: pd.DataFrame,
    classifications: pd.DataFrame,
    universe_lookup: dict[date, pd.DataFrame],
    execution_map: pd.DataFrame,
    candidate: ResearchCandidate,
    *,
    round_trip_cost_bps: float,
    initial_equity: float = DEFAULT_INITIAL_EQUITY,
    min_scored_symbols: int | None = None,
    open_returns: pd.DataFrame | None = None,
    score_panel_cache: Any | None = None,
    prepared_cache_key: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    min_scored = int(min_scored_symbols or _min_scored_requirement(top_n=candidate.top_n, book_mode=candidate.book_mode))
    signal_dates = execution_map["signal_date"].drop_duplicates().tolist()
    score_panel: pd.DataFrame | None = None
    score_cache_key: str | None = None
    if score_panel_cache is not None and prepared_cache_key:
        score_cache_key = score_panel_cache.build_score_key(
            prepared_key=prepared_cache_key,
            alpha_name=candidate.alpha_name,
            params=candidate.params,
            group_level=candidate.group_level,
            signal_decay=candidate.signal_decay,
            score_truncation=candidate.score_truncation,
            min_scored_symbols=min_scored,
        )
        score_panel = score_panel_cache.load_score_panel(prepared_cache_key, score_cache_key)
    if score_panel is None:
        score_panel, _ = compute_alpha_score_panel(
            candidate.alpha_name,
            bars,
            classifications=classifications,
            sector_map=None,
            group_level=candidate.group_level,
            params=candidate.params,
            score_truncation=candidate.score_truncation,
            signal_decay=candidate.signal_decay,
            signal_dates=signal_dates,
            min_scored_symbols=min_scored,
        )
        if score_panel_cache is not None and prepared_cache_key and score_cache_key and not score_panel.empty:
            score_panel_cache.save_score_panel(prepared_cache_key, score_cache_key, score_panel)
    if score_panel.empty:
        raise BacktestError(f"Candidate {candidate.name} produced no score panel.")

    open_returns = open_returns.copy() if open_returns is not None else _build_open_return_frame(bars)
    equity = float(initial_equity)
    previous_weights = pd.Series(dtype="float64")
    daily_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    position_rows: list[dict[str, Any]] = []

    for _, schedule_row in execution_map.iterrows():
        execution_date = schedule_row["execution_date"]
        signal_date = schedule_row["signal_date"]
        next_execution_date = schedule_row["next_execution_date"]
        universe = universe_lookup.get(signal_date)
        if universe is None or universe.empty:
            raise BacktestError(f"No historical universe available for signal date {signal_date}.")

        signal_slice = score_panel[
            (score_panel["trade_date"] == signal_date)
            & (score_panel["symbol"].isin(universe["symbol"]))
        ].copy()
        if signal_slice.empty:
            raise BacktestError(f"No scored slice available for signal date {signal_date}.")

        signal_scores = signal_slice[["symbol", "score", "sector"]].copy()
        build = build_sector_neutral_targets(
            signal_scores,
            equity=equity,
            top_n=candidate.top_n,
            gross_exposure=candidate.gross_exposure,
            book_mode=candidate.book_mode,
            shortable_map=None,
        )
        targets = build.targets.copy()
        current_weights = _align_weight_series(targets)
        turnover = _period_turnover(previous_weights, current_weights)
        cost_rate = float(round_trip_cost_bps) / 10_000.0
        cost_return = turnover * cost_rate

        period_slice = open_returns[
            (open_returns["trade_date"] == execution_date)
            & (open_returns["symbol"].isin(current_weights.index))
        ].copy()
        merged_returns = (
            period_slice.merge(
                targets[["symbol", "target_weight", "sector", "score"]],
                on="symbol",
                how="right",
            )
            .fillna({"period_return": 0.0, "target_weight": 0.0})
            .reset_index(drop=True)
        )
        gross_return = float(
            (merged_returns["target_weight"].astype(float) * merged_returns["period_return"].astype(float)).sum()
        )
        net_return = gross_return - cost_return
        next_equity = float(equity * (1.0 + net_return))

        long_count = int((targets["side"] == "long").sum()) if not targets.empty else 0
        short_count = int((targets["side"] == "short").sum()) if not targets.empty else 0
        daily_rows.append(
            {
                "execution_date": execution_date.isoformat(),
                "signal_date": signal_date.isoformat(),
                "next_execution_date": next_execution_date.isoformat(),
                "daily_return": net_return,
                "daily_return_gross": gross_return,
                "turnover": turnover,
                "cost_return": cost_return,
                "equity": next_equity,
                "equity_start": equity,
                "equity_end": next_equity,
                "candidate_name": candidate.name,
                "alpha_name": candidate.alpha_name,
                "family": candidate.family,
                "group_level": candidate.group_level,
                "book_mode": candidate.book_mode,
                "top_n": candidate.top_n,
                "gross_exposure": candidate.gross_exposure,
                "signal_decay": candidate.signal_decay,
                "score_truncation": candidate.score_truncation,
                "long_count": long_count,
                "short_count": short_count,
                "full_book": _is_full_book(
                    long_count=long_count,
                    short_count=short_count,
                    top_n=candidate.top_n,
                    book_mode=candidate.book_mode,
                ),
            }
        )
        if not targets.empty:
            target_export = targets.copy()
            target_export["execution_date"] = execution_date.isoformat()
            target_export["signal_date"] = signal_date.isoformat()
            target_export["candidate_name"] = candidate.name
            target_export["alpha_name"] = candidate.alpha_name
            target_rows.extend(target_export.to_dict("records"))

            position_export = targets.copy()
            position_export["execution_date"] = execution_date.isoformat()
            position_export["next_execution_date"] = next_execution_date.isoformat()
            position_export["candidate_name"] = candidate.name
            position_export["alpha_name"] = candidate.alpha_name
            position_rows.extend(position_export.to_dict("records"))

        previous_weights = current_weights
        equity = next_equity

    daily_frame = pd.DataFrame(daily_rows)
    if daily_frame.empty:
        raise BacktestError(f"Research candidate {candidate.name} produced no daily rows.")
    return daily_frame, pd.DataFrame(target_rows), pd.DataFrame(position_rows)


def _compute_sector_concentration(targets: pd.DataFrame) -> tuple[float, float]:
    if targets.empty:
        return 0.0, 0.0
    work = targets.copy()
    work["abs_weight"] = work["target_weight"].astype(float).abs()
    sector_weights = (
        work.groupby(["execution_date", "sector"], as_index=False)["abs_weight"].sum()
        .sort_values(["execution_date", "abs_weight"], ascending=[True, False])
    )
    total_abs = work.groupby("execution_date", as_index=False)["abs_weight"].sum().rename(
        columns={"abs_weight": "total_abs"}
    )
    sector_weights = sector_weights.merge(total_abs, on="execution_date", how="left")
    sector_weights["sector_share"] = sector_weights["abs_weight"].astype(float).div(
        sector_weights["total_abs"].astype(float).replace(0, pd.NA)
    )
    sector_weights["sector_share"] = sector_weights["sector_share"].fillna(0.0)
    sector_max = float(sector_weights["sector_share"].max()) if not sector_weights.empty else 0.0
    top3 = (
        sector_weights.groupby("execution_date")["sector_share"]
        .apply(lambda s: float(pd.Series(s).nlargest(3).sum()))
        .reset_index(name="top3_share")
    )
    top3_mean = float(top3["top3_share"].mean()) if not top3.empty else 0.0
    return sector_max, top3_mean


def positive_month_ratio(daily_frame: pd.DataFrame) -> float:
    if daily_frame.empty:
        return 0.0
    work = daily_frame.copy()
    work["execution_date"] = pd.to_datetime(work["execution_date"], errors="coerce")
    work["month"] = work["execution_date"].dt.to_period("M").astype(str)
    month_returns = work.groupby("month")["daily_return"].apply(lambda s: float((1.0 + s).prod() - 1.0))
    if month_returns.empty:
        return 0.0
    return float((month_returns > 0).mean())


def summarize_research_candidate(
    daily_frame: pd.DataFrame,
    targets: pd.DataFrame,
    candidate: ResearchCandidate,
) -> dict[str, Any]:
    metrics = compute_proxy_metrics(daily_frame.rename(columns={"execution_date": "trade_date"}))
    sector_concentration_max, sector_concentration_mean_top3 = _compute_sector_concentration(targets)
    long_count_mean = float(daily_frame["long_count"].mean()) if not daily_frame.empty else 0.0
    short_count_mean = float(daily_frame["short_count"].mean()) if not daily_frame.empty else 0.0
    full_book_ratio = float(daily_frame["full_book"].astype(float).mean()) if not daily_frame.empty else 0.0
    return {
        **candidate.to_dict(),
        **metrics,
        "positive_month_ratio": positive_month_ratio(daily_frame),
        "sector_concentration_max": sector_concentration_max,
        "sector_concentration_mean_top3": sector_concentration_mean_top3,
        "long_count_mean": long_count_mean,
        "short_count_mean": short_count_mean,
        "days_with_full_book_ratio": full_book_ratio,
    }


def rank_research_candidates(frame: pd.DataFrame, *, prefix: str = "") -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    columns = [
        f"{prefix}fitness_proxy",
        f"{prefix}sharpe_proxy",
        f"{prefix}returns",
        f"{prefix}max_drawdown",
        f"{prefix}turnover_mean",
    ]
    ranked = frame.sort_values(
        columns,
        ascending=[False, False, False, True, True],
    ).reset_index(drop=True)
    ranked["rank"] = ranked.index + 1
    return ranked


def build_candidate_correlation(candidate_daily_frames: dict[str, pd.DataFrame], *, split_name: str = "oos") -> pd.DataFrame:
    series_map: dict[str, pd.Series] = {}
    for candidate_name, daily in candidate_daily_frames.items():
        subset = daily[daily["split"] == split_name].copy()
        if subset.empty:
            continue
        series = subset.set_index("execution_date")["daily_return"].astype(float).sort_index()
        series_map[candidate_name] = series
    if not series_map:
        return pd.DataFrame()
    matrix = pd.DataFrame(series_map).corr().reset_index().rename(columns={"index": "candidate_name"})
    return matrix


def build_basket_daily_returns(
    member_names: list[str],
    candidate_daily_frames: dict[str, pd.DataFrame],
    *,
    weights: dict[str, float],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for member_name in member_names:
        daily = candidate_daily_frames[member_name].copy()
        daily["member_weight"] = float(weights.get(member_name, 0.0))
        daily["weighted_return"] = daily["daily_return"].astype(float) * daily["member_weight"]
        daily["weighted_turnover"] = daily["turnover"].astype(float) * abs(daily["member_weight"])
        frames.append(
            daily[
                [
                    "execution_date",
                    "split",
                    "weighted_return",
                    "weighted_turnover",
                    "member_weight",
                ]
            ]
        )
    combined = pd.concat(frames, ignore_index=True)
    grouped = combined.groupby(["execution_date", "split"], as_index=False).agg(
        daily_return=("weighted_return", "sum"),
        turnover=("weighted_turnover", "sum"),
    )
    grouped = grouped.sort_values("execution_date").reset_index(drop=True)
    equity = DEFAULT_INITIAL_EQUITY
    equity_values: list[float] = []
    for daily_return in grouped["daily_return"].astype(float):
        equity *= 1.0 + daily_return
        equity_values.append(equity)
    grouped["equity"] = equity_values
    return grouped


def build_basket_targets(
    member_names: list[str],
    candidate_targets: dict[str, pd.DataFrame],
    *,
    weights: dict[str, float],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for member_name in member_names:
        targets = candidate_targets.get(member_name)
        if targets is None or targets.empty:
            continue
        member_weight = float(weights.get(member_name, 0.0))
        frame = targets.copy()
        frame["target_weight"] = frame["target_weight"].astype(float) * member_weight
        frame["target_notional"] = frame["target_notional"].astype(float) * member_weight
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = (
        out.groupby(["execution_date", "signal_date", "symbol", "sector"], as_index=False)
        .agg(target_weight=("target_weight", "sum"), target_notional=("target_notional", "sum"), score=("score", "mean"))
    )
    out = out[out["target_weight"].abs() > 0].copy()
    out["side"] = out["target_weight"].map(lambda value: "long" if float(value) >= 0 else "short")
    return out.sort_values(["execution_date", "symbol"]).reset_index(drop=True)


def write_research_outputs(
    outputs: dict[str, Any],
    *,
    research_runs_dir: Path,
    run_stamp: str,
) -> Path:
    run_dir = research_runs_dir / run_stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    outputs["family_leaderboard"].to_csv(run_dir / "family_leaderboard.csv", index=False)
    outputs["candidate_leaderboard"].to_csv(run_dir / "candidate_leaderboard.csv", index=False)
    outputs["oos_survivors"].to_csv(run_dir / "oos_survivors.csv", index=False)
    outputs["unseen_results"].to_csv(run_dir / "unseen_results.csv", index=False)
    outputs["candidate_correlation"].to_csv(run_dir / "candidate_correlation.csv", index=False)
    outputs["split_metrics"].to_csv(run_dir / "split_metrics.csv", index=False)
    outputs["selected_strategy_daily_equity"].to_csv(run_dir / "selected_strategy_daily_equity.csv", index=False)
    outputs["selected_strategy_daily_positions"].to_csv(run_dir / "selected_strategy_daily_positions.csv", index=False)
    outputs["selected_strategy_daily_targets"].to_csv(run_dir / "daily_targets.csv", index=False)
    outputs["iex_robustness"].to_csv(run_dir / "iex_robustness_split_metrics.csv", index=False)
    (run_dir / "promotion_report.md").write_text(outputs["promotion_report"], encoding="utf-8")
    (run_dir / "metadata.json").write_text(json.dumps(outputs["metadata"], indent=2), encoding="utf-8")
    write_strategy_spec(run_dir / "selected_strategy.json", outputs["selected_strategy"])
    return run_dir
