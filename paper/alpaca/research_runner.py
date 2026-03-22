from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from alpha_registry import StrategyMember, StrategySpec, selected_strategy_path, write_strategy_spec
from backtest_engine import (
    BacktestError,
    ResearchCandidate,
    _build_open_return_frame,
    _build_execution_calendar_maps,
    build_basket_daily_returns,
    build_basket_targets,
    build_candidate_correlation,
    build_split_windows,
    build_universe_lookup,
    canonicalize_bars,
    expand_research_candidates,
    positive_month_ratio,
    run_research_candidate,
    summarize_research_candidate,
    write_research_outputs,
)
from broker_alpaca import AlpacaBroker
from classification_store import (
    ClassificationStoreError,
    load_classifications_snapshot,
    load_symbol_master,
    resolve_classification_snapshot_path,
)
from config import load_config, parse_trade_date
from historical_store import HistoricalStore
from monthly_eval import compute_proxy_metrics
from research_baseline import ResearchBaseline, load_research_baseline
from research_materialized_cache import ResearchMaterializedCache


DEFAULT_FEED = "sip"
DEFAULT_ALPHA_SET = "literature_core"
DEFAULT_GROUP_LEVEL_GRID = ["market", "sector", "industry"]
DEFAULT_BOOK_MODE_GRID = ["sector_weighted"]
DEFAULT_TOP_N_GRID = [3000]
DEFAULT_DECAY_GRID = [0, 3, 5]
DEFAULT_TRUNCATION_GRID = [None, 0.05, 0.10]
DEFAULT_PROMOTION_PROFILE = "balanced"
DEFAULT_MAX_PER_FAMILY = 3
DEFAULT_MIN_UNIVERSE = 2500
DEFAULT_MIN_UNIVERSE_RATIO = 0.90
FALLBACK_TRAIN_DAYS = 756


def _parse_int_grid(raw: str, default: list[int]) -> list[int]:
    text = str(raw or "").strip()
    if not text:
        return list(default)
    return [int(token.strip()) for token in text.split(",") if token.strip()]


def _parse_str_grid(raw: str, default: list[str]) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return list(default)
    return [token.strip().lower() for token in text.split(",") if token.strip()]


def _parse_truncation_grid(raw: str, default: list[float | None]) -> list[float | None]:
    text = str(raw or "").strip()
    if not text:
        return list(default)
    values: list[float | None] = []
    for token in text.split(","):
        normalized = token.strip().lower()
        if not normalized:
            continue
        if normalized in {"none", "null"}:
            values.append(None)
        else:
            values.append(float(normalized))
    return values


def _coverage_ratio(universe_lookup: dict[date, pd.DataFrame], signal_dates: list[date], *, min_symbols: int) -> float:
    if not signal_dates:
        return 0.0
    valid_days = 0
    for signal_date in signal_dates:
        frame = universe_lookup.get(signal_date)
        if frame is not None and len(frame) >= int(min_symbols):
            valid_days += 1
    return float(valid_days / len(signal_dates))


def _prepare_inputs(
    *,
    cfg: Any,
    broker: AlpacaBroker,
    end_date: date,
    classification_snapshot_date: date | None = None,
    feed: str,
    train_days: int,
    oos_days: int,
    test_days: int,
    min_universe: int = DEFAULT_MIN_UNIVERSE,
    min_universe_ratio: float = DEFAULT_MIN_UNIVERSE_RATIO,
) -> dict[str, Any]:
    materialized_cache = ResearchMaterializedCache(cfg.cache_dir)
    try:
        classification_snapshot_path = resolve_classification_snapshot_path(
            cfg.reference_dir,
            snapshot_date=classification_snapshot_date or end_date,
        )
        classifications = load_classifications_snapshot(
            cfg.reference_dir,
            snapshot_date=classification_snapshot_date or end_date,
        )
        symbol_master = load_symbol_master(cfg.reference_dir)
    except ClassificationStoreError as exc:
        raise BacktestError(str(exc)) from exc

    if not classifications.empty:
        candidate_symbols = sorted(set(classifications["symbol"].astype(str).str.upper().tolist()))
    else:
        candidate_symbols = sorted(set(symbol_master["symbol"].astype(str).str.upper().tolist()))
    if not candidate_symbols:
        raise BacktestError("Classification cache produced no candidate symbols.")

    prepared_key = materialized_cache.build_prepared_key(
        end_date=end_date,
        classification_snapshot_path=classification_snapshot_path,
        classification_snapshot_date=classification_snapshot_date or end_date,
        feed=feed,
        train_days=train_days,
        oos_days=oos_days,
        test_days=test_days,
        min_universe=min_universe,
        min_universe_ratio=min_universe_ratio,
    )
    cached = materialized_cache.load_prepared_inputs(prepared_key)
    if cached is not None:
        cached["classifications"] = classifications
        cached["symbol_master"] = symbol_master
        cached["store"] = HistoricalStore(cfg.cache_dir)
        cached["materialized_cache"] = materialized_cache
        return cached

    store = HistoricalStore(cfg.cache_dir)
    trading_days = store.load_trading_calendar(
        start=end_date - timedelta(days=3650),
        end=end_date,
        broker=broker,
    )

    degraded_depth = False
    last_error: Exception | None = None
    for train_window in [int(train_days), FALLBACK_TRAIN_DAYS]:
        if train_window != int(train_days):
            degraded_depth = True
        try:
            splits = build_split_windows(
                trading_days,
                end_date=end_date,
                train_days=train_window,
                oos_days=int(oos_days),
                test_days=int(test_days),
            )
        except Exception as exc:
            last_error = exc
            continue

        execution_map = _build_execution_calendar_maps(splits.execution_dates, trading_days)
        earliest_signal = execution_map["signal_date"].min()
        if pd.isna(earliest_signal):
            raise BacktestError("Unable to determine earliest signal date for research run.")
        fetch_start = pd.Timestamp(earliest_signal).date() - timedelta(days=260)

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
        coverage_ratio = _coverage_ratio(
            universe_lookup,
            signal_dates,
            min_symbols=int(min_universe),
        )
        if coverage_ratio >= float(min_universe_ratio):
            prepared = {
                "prepared_cache_key": prepared_key,
                "end_date": end_date.isoformat(),
                "feed": str(feed).strip().lower(),
                "min_universe": int(min_universe),
                "min_universe_ratio": float(min_universe_ratio),
                "classification_snapshot_path": classification_snapshot_path,
                "classification_snapshot_date": (
                    classification_snapshot_date or end_date
                ).isoformat(),
                "classifications": classifications,
                "symbol_master": symbol_master,
                "store": store,
                "trading_days": trading_days,
                "splits": splits,
                "execution_map": execution_map,
                "bars": canonical_bars,
                "open_returns": _build_open_return_frame(canonical_bars),
                "universe_lookup": universe_lookup,
                "coverage_ratio": coverage_ratio,
                "degraded_depth": degraded_depth,
                "materialized_cache": materialized_cache,
            }
            materialized_cache.save_prepared_inputs(prepared)
            return prepared
        last_error = BacktestError(
            f"Universe coverage ratio {coverage_ratio:.3f} below required {float(min_universe_ratio):.2f}"
        )
        if train_window == FALLBACK_TRAIN_DAYS:
            break

    raise BacktestError(str(last_error or "Unable to prepare research inputs."))


def _prefix_summary(summary: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {
        f"{prefix}{key}": value
        for key, value in summary.items()
        if key
        not in {
            "alpha_name",
            "family",
            "params",
            "group_level",
            "book_mode",
            "top_n",
            "gross_exposure",
            "signal_decay",
            "score_truncation",
            "candidate_name",
        }
    }


def _filter_oos_survivors(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    mask = (
        (frame["oos_returns"] > 0)
        & (frame["oos_fitness_proxy"] > 0)
        & (frame["oos_sharpe_proxy"] > 0.5)
        & (frame["oos_max_drawdown"] < 0.25)
        & (frame["oos_days_with_full_book_ratio"] >= 0.90)
    )
    return frame[mask].copy().reset_index(drop=True)


def _filter_unseen_passers(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    mask = (
        (frame["test_returns"] > 0)
        & (frame["test_fitness_proxy"] > 0.25)
        & (frame["test_sharpe_proxy"] > 0.8)
        & (frame["test_max_drawdown"] < 0.20)
        & (frame["test_turnover_mean"] < 6.0)
        & (frame["test_positive_month_ratio"] >= 0.55)
    )
    return frame[mask].copy().reset_index(drop=True)


def _candidate_rank_columns(prefix: str) -> list[str]:
    return [
        f"{prefix}fitness_proxy",
        f"{prefix}sharpe_proxy",
        f"{prefix}returns",
        f"{prefix}max_drawdown",
        f"{prefix}turnover_mean",
    ]


def _sort_candidates(frame: pd.DataFrame, *, prefix: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return frame.sort_values(
        _candidate_rank_columns(prefix),
        ascending=[False, False, False, True, True],
    ).reset_index(drop=True)


def _apply_sector_vs_none_rule(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    sector_modes = {"sector", "sector_weighted"}
    none_modes = {"none", "none_weighted"}
    sector_best = _sort_candidates(frame[frame["book_mode"].isin(sector_modes)].copy(), prefix="test_")
    none_best = _sort_candidates(frame[frame["book_mode"].isin(none_modes)].copy(), prefix="test_")
    if sector_best.empty or none_best.empty:
        return frame.copy()
    best_sector = sector_best.iloc[0]
    best_none = none_best.iloc[0]
    none_beats_sector = (
        best_none["test_returns"] >= best_sector["test_returns"] * 1.20
        and best_none["test_fitness_proxy"] >= best_sector["test_fitness_proxy"] + 0.10
        and best_none["test_max_drawdown"] <= best_sector["test_max_drawdown"] + 0.02
        and best_none["test_sector_concentration_max"] <= 0.45
    )
    if none_beats_sector:
        return frame.copy()
    return frame[frame["book_mode"].isin(sector_modes)].copy().reset_index(drop=True)


def _build_strategy_from_row(row: pd.Series, *, approved: bool, source_run_id: str, feed: str) -> StrategySpec:
    member = StrategyMember(
        name=str(row["candidate_name"]),
        alpha_name=str(row["alpha_name"]),
        family=str(row["family"]),
        weight=1.0,
        params=dict(row["params"]),
        group_level=str(row["group_level"]),
        book_mode=str(row["book_mode"]),
        top_n=int(row["top_n"]),
        signal_decay=int(row["signal_decay"]),
        score_truncation=(
            None
            if row.get("score_truncation", None) in {"", None} or pd.isna(row.get("score_truncation"))
            else float(row["score_truncation"])
        ),
    )
    return StrategySpec(
        strategy_type="single",
        feed=feed,
        gross_exposure=float(row["gross_exposure"]),
        book_mode=str(row["book_mode"]),
        top_n=int(row["top_n"]),
        group_level=str(row["group_level"]),
        members=[member],
        approved=approved,
        source_run_id=source_run_id,
        promotion_profile=DEFAULT_PROMOTION_PROFILE,
    )


def _build_basket_strategy(
    candidate_frame: pd.DataFrame,
    candidate_daily_frames: dict[str, pd.DataFrame],
    candidate_targets: dict[str, pd.DataFrame],
    *,
    source_run_id: str,
    feed: str,
) -> tuple[StrategySpec, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    eligible = _sort_candidates(candidate_frame, prefix="test_")
    if eligible.empty:
        raise BacktestError("Cannot build basket from an empty candidate frame.")

    corr = build_candidate_correlation(candidate_daily_frames, split_name="oos")
    if corr.empty:
        selected_names = [str(eligible.iloc[0]["candidate_name"])]
    else:
        corr_indexed = corr.set_index("candidate_name")
        selected_names = [str(eligible.iloc[0]["candidate_name"])]
        for _, row in eligible.iloc[1:].iterrows():
            candidate_name = str(row["candidate_name"])
            if len(selected_names) >= 4:
                break
            allowed = True
            for selected in selected_names:
                if selected not in corr_indexed.columns or candidate_name not in corr_indexed.index:
                    continue
                if float(corr_indexed.loc[candidate_name, selected]) >= 0.35:
                    allowed = False
                    break
            if allowed:
                selected_names.append(candidate_name)

    weight = 1.0 / len(selected_names)
    weights = {name: weight for name in selected_names}
    basket_daily = build_basket_daily_returns(selected_names, candidate_daily_frames, weights=weights)
    basket_targets = build_basket_targets(selected_names, candidate_targets, weights=weights)
    basket_positions = basket_targets.copy()
    if not basket_positions.empty:
        basket_positions["next_execution_date"] = basket_positions["execution_date"]

    split_rows: list[dict[str, Any]] = []
    for split_name in ["train", "oos", "test"]:
        subset = basket_daily[basket_daily["split"] == split_name].copy()
        metrics = compute_proxy_metrics(subset.rename(columns={"execution_date": "trade_date"}))
        split_rows.append({"split": split_name, **metrics})
    split_metrics = pd.DataFrame(split_rows)

    members: list[StrategyMember] = []
    for candidate_name in selected_names:
        row = candidate_frame[candidate_frame["candidate_name"] == candidate_name].iloc[0]
        members.append(
            StrategyMember(
                name=candidate_name,
                alpha_name=str(row["alpha_name"]),
                family=str(row["family"]),
                weight=weight,
                params=dict(row["params"]),
                group_level=str(row["group_level"]),
                book_mode=str(row["book_mode"]),
                top_n=int(row["top_n"]),
                signal_decay=int(row["signal_decay"]),
                score_truncation=(
                    None
                    if row.get("score_truncation", None) in {"", None} or pd.isna(row.get("score_truncation"))
                    else float(row["score_truncation"])
                ),
            )
        )

    strategy = StrategySpec(
        strategy_type="basket",
        feed=feed,
        gross_exposure=float(candidate_frame.iloc[0]["gross_exposure"]),
        book_mode=str(candidate_frame.iloc[0]["book_mode"]),
        top_n=int(candidate_frame.iloc[0]["top_n"]),
        group_level="mixed" if len(set(candidate_frame["group_level"])) > 1 else str(candidate_frame.iloc[0]["group_level"]),
        members=members,
        approved=True,
        source_run_id=source_run_id,
        promotion_profile=DEFAULT_PROMOTION_PROFILE,
    )
    return strategy, basket_daily, basket_targets, basket_positions, split_metrics


def _split_metrics_for_single(row: pd.Series) -> pd.DataFrame:
    split_rows = []
    for split_name, prefix in [("train", "train_"), ("oos", "oos_"), ("test", "test_")]:
        split_rows.append(
            {
                "split": split_name,
                "fitness_proxy": row[f"{prefix}fitness_proxy"],
                "sharpe_proxy": row[f"{prefix}sharpe_proxy"],
                "returns": row[f"{prefix}returns"],
                "max_drawdown": row[f"{prefix}max_drawdown"],
                "turnover_mean": row[f"{prefix}turnover_mean"],
            }
        )
    return pd.DataFrame(split_rows)


def _run_selected_iex_robustness(
    *,
    cfg: Any,
    broker: AlpacaBroker,
    end_date: date,
    classification_snapshot_date: date,
    strategy: StrategySpec,
    train_days: int,
    oos_days: int,
    test_days: int,
    min_universe: int,
    min_universe_ratio: float,
) -> pd.DataFrame:
    prepared = _prepare_inputs(
        cfg=cfg,
        broker=broker,
        end_date=end_date,
        classification_snapshot_date=classification_snapshot_date,
        feed="iex",
        train_days=train_days,
        oos_days=oos_days,
        test_days=test_days,
        min_universe=min_universe,
        min_universe_ratio=min_universe_ratio,
    )
    execution_map = prepared["execution_map"]
    split_map = {
        execution_date.isoformat(): name
        for name, dates in [
            ("train", prepared["splits"].train_dates),
            ("oos", prepared["splits"].oos_dates),
            ("test", prepared["splits"].test_dates),
        ]
        for execution_date in dates
    }

    daily_frames: dict[str, pd.DataFrame] = {}
    for member in strategy.members:
        candidate = ResearchCandidate(
            alpha_name=member.alpha_name,
            family=member.family,
            params=member.params,
            group_level=member.group_level,
            book_mode=member.book_mode,
            top_n=member.top_n,
            gross_exposure=strategy.gross_exposure,
            signal_decay=member.signal_decay,
            score_truncation=member.score_truncation,
        )
        daily, _, _ = run_research_candidate(
            prepared["bars"],
            prepared["classifications"],
            prepared["universe_lookup"],
            execution_map,
            candidate,
            round_trip_cost_bps=cfg.round_trip_cost_bps,
            open_returns=prepared.get("open_returns"),
            score_panel_cache=prepared.get("materialized_cache"),
            prepared_cache_key=prepared.get("prepared_cache_key"),
        )
        daily["split"] = daily["execution_date"].map(split_map)
        daily_frames[candidate.name] = daily

    if strategy.strategy_type == "basket":
        weights = {member.name: member.weight for member in strategy.members}
        basket_daily = build_basket_daily_returns([member.name for member in strategy.members], daily_frames, weights=weights)
        rows = []
        for split_name in ["train", "oos", "test"]:
            metrics = compute_proxy_metrics(basket_daily[basket_daily["split"] == split_name].rename(columns={"execution_date": "trade_date"}))
            rows.append({"split": split_name, **metrics})
        return pd.DataFrame(rows)

    member_daily = daily_frames[strategy.members[0].name]
    rows = []
    for split_name in ["train", "oos", "test"]:
        metrics = compute_proxy_metrics(member_daily[member_daily["split"] == split_name].rename(columns={"execution_date": "trade_date"}))
        rows.append({"split": split_name, **metrics})
    return pd.DataFrame(rows)


def _load_baseline(cfg: Any, args: argparse.Namespace) -> ResearchBaseline:
    baseline_file = Path(str(args.baseline_file or cfg.research_baseline_file))
    return load_research_baseline(baseline_file)


def _resolve_research_args(cfg: Any, args: argparse.Namespace) -> dict[str, Any]:
    baseline = _load_baseline(cfg, args)
    dynamic_baseline = bool(args.dynamic_baseline)
    end_date = parse_trade_date(args.end_date) if args.end_date else baseline.end_date
    return {
        "baseline": baseline,
        "dynamic_baseline": dynamic_baseline,
        "end_date": end_date,
        "feed": str(args.feed).strip().lower() or baseline.feed,
        "train_days": int(args.train_days or baseline.train_days),
        "oos_days": int(args.oos_days or baseline.oos_days),
        "test_days": int(args.test_days or baseline.test_days),
        "alpha_set": str(args.alpha_set).strip() or baseline.alpha_set,
        "group_level_grid": _parse_str_grid(args.group_level_grid, baseline.group_level_grid),
        "book_mode_grid": _parse_str_grid(args.book_mode_grid, baseline.book_mode_grid),
        "top_n_grid": _parse_int_grid(args.top_n_grid, baseline.top_n_grid),
        "decay_grid": _parse_int_grid(args.decay_grid, baseline.decay_grid),
        "truncation_grid": _parse_truncation_grid(args.truncation_grid, baseline.truncation_grid),
        "classification_snapshot_date": end_date if dynamic_baseline else baseline.classification_snapshot_date,
    }


def run_research(args: argparse.Namespace) -> int:
    cfg = load_config()
    resolved = _resolve_research_args(cfg, args)
    baseline = resolved["baseline"]
    end_date = resolved["end_date"]
    api_key, api_secret = cfg.require_alpaca_credentials()
    broker = AlpacaBroker(api_key, api_secret, paper=True)

    prepared = _prepare_inputs(
        cfg=cfg,
        broker=broker,
        end_date=end_date,
        classification_snapshot_date=resolved["classification_snapshot_date"],
        feed=resolved["feed"],
        train_days=resolved["train_days"],
        oos_days=resolved["oos_days"],
        test_days=resolved["test_days"],
        min_universe=baseline.min_universe,
        min_universe_ratio=baseline.min_universe_ratio,
    )
    group_level_grid = resolved["group_level_grid"]
    book_mode_grid = resolved["book_mode_grid"]
    top_n_grid = resolved["top_n_grid"]
    decay_grid = resolved["decay_grid"]
    truncation_grid = resolved["truncation_grid"]
    execution_map = prepared["execution_map"]
    split_map = {
        execution_date.isoformat(): name
        for name, dates in [
            ("train", prepared["splits"].train_dates),
            ("oos", prepared["splits"].oos_dates),
            ("test", prepared["splits"].test_dates),
        ]
        for execution_date in dates
    }

    all_candidates = expand_research_candidates(
        alpha_set=resolved["alpha_set"],
        group_level_grid=group_level_grid,
        book_mode_grid=book_mode_grid,
        top_n_grid=top_n_grid,
        decay_grid=decay_grid,
        truncation_grid=truncation_grid,
        gross_exposure=float(baseline.gross_exposure),
    )

    train_map = execution_map[execution_map["execution_date"].isin(prepared["splits"].train_dates)].reset_index(drop=True)
    train_rows: list[dict[str, Any]] = []
    for candidate in all_candidates:
        daily, targets, _ = run_research_candidate(
            prepared["bars"],
            prepared["classifications"],
            prepared["universe_lookup"],
            train_map,
            candidate,
            round_trip_cost_bps=cfg.round_trip_cost_bps,
            open_returns=prepared.get("open_returns"),
            score_panel_cache=prepared.get("materialized_cache"),
            prepared_cache_key=prepared.get("prepared_cache_key"),
        )
        train_rows.append(summarize_research_candidate(daily, targets, candidate))

    train_leaderboard = _sort_candidates(pd.DataFrame(train_rows), prefix="")
    family_leaderboard = (
        train_leaderboard.groupby("family", as_index=False)
        .head(int(args.max_candidates_per_family))
        .sort_values(["family", "fitness_proxy", "sharpe_proxy"], ascending=[True, False, False])
        .reset_index(drop=True)
    )
    finalists = train_leaderboard.groupby("family", as_index=False).head(int(args.max_candidates_per_family)).reset_index(drop=True)

    candidate_rows: list[dict[str, Any]] = []
    candidate_daily_frames: dict[str, pd.DataFrame] = {}
    candidate_targets: dict[str, pd.DataFrame] = {}
    candidate_positions: dict[str, pd.DataFrame] = {}
    for _, row in finalists.iterrows():
        candidate = ResearchCandidate(
            alpha_name=str(row["alpha_name"]),
            family=str(row["family"]),
            params=dict(row["params"]),
            group_level=str(row["group_level"]),
            book_mode=str(row["book_mode"]),
            top_n=int(row["top_n"]),
            gross_exposure=float(row["gross_exposure"]),
            signal_decay=int(row["signal_decay"]),
            score_truncation=(
                None
                if row.get("score_truncation", None) in {"", None} or pd.isna(row.get("score_truncation"))
                else float(row["score_truncation"])
            ),
        )
        daily, targets, positions = run_research_candidate(
            prepared["bars"],
            prepared["classifications"],
            prepared["universe_lookup"],
            execution_map,
            candidate,
            round_trip_cost_bps=cfg.round_trip_cost_bps,
            open_returns=prepared.get("open_returns"),
            score_panel_cache=prepared.get("materialized_cache"),
            prepared_cache_key=prepared.get("prepared_cache_key"),
        )
        daily["split"] = daily["execution_date"].map(split_map)
        if not targets.empty:
            targets["split"] = targets["execution_date"].map(split_map)
        if not positions.empty:
            positions["split"] = positions["execution_date"].map(split_map)
        candidate_daily_frames[candidate.name] = daily
        candidate_targets[candidate.name] = targets
        candidate_positions[candidate.name] = positions

        train_summary = summarize_research_candidate(daily[daily["split"] == "train"].copy(), targets[targets["split"] == "train"].copy(), candidate)
        oos_summary = summarize_research_candidate(daily[daily["split"] == "oos"].copy(), targets[targets["split"] == "oos"].copy(), candidate)
        test_summary = summarize_research_candidate(daily[daily["split"] == "test"].copy(), targets[targets["split"] == "test"].copy(), candidate)
        candidate_rows.append(
            {
                **candidate.to_dict(),
                **_prefix_summary(train_summary, "train_"),
                **_prefix_summary(oos_summary, "oos_"),
                **_prefix_summary(test_summary, "test_"),
            }
        )

    candidate_leaderboard = _sort_candidates(pd.DataFrame(candidate_rows), prefix="train_")
    oos_survivors = _sort_candidates(_filter_oos_survivors(candidate_leaderboard), prefix="oos_")
    unseen_passers = _sort_candidates(_filter_unseen_passers(oos_survivors), prefix="test_")
    promotion_pool = _sort_candidates(_apply_sector_vs_none_rule(unseen_passers), prefix="test_")

    correlation_base = promotion_pool if not promotion_pool.empty else oos_survivors
    correlation_frames = {
        candidate_name: candidate_daily_frames[candidate_name]
        for candidate_name in correlation_base["candidate_name"].tolist()
        if candidate_name in candidate_daily_frames
    }
    candidate_correlation = build_candidate_correlation(correlation_frames, split_name="oos")

    selected_strategy: StrategySpec
    selected_daily: pd.DataFrame
    selected_targets: pd.DataFrame
    selected_positions: pd.DataFrame
    split_metrics: pd.DataFrame
    promotion_notes: list[str] = []

    if promotion_pool.empty:
        fallback = _sort_candidates(candidate_leaderboard, prefix="test_").iloc[0]
        selected_strategy = _build_strategy_from_row(
            fallback,
            approved=False,
            source_run_id="",
            feed=str(args.feed).strip().lower() or DEFAULT_FEED,
        )
        selected_daily = candidate_daily_frames[str(fallback["candidate_name"])].copy()
        selected_targets = candidate_targets[str(fallback["candidate_name"])].copy()
        selected_positions = candidate_positions[str(fallback["candidate_name"])].copy()
        split_metrics = _split_metrics_for_single(fallback)
        promotion_notes.append("No candidate passed the balanced unseen promotion bar. Best shadow candidate retained.")
    else:
        best_single = _sort_candidates(promotion_pool, prefix="test_").iloc[0]
        selected_strategy = _build_strategy_from_row(
            best_single,
            approved=True,
            source_run_id="",
            feed=str(args.feed).strip().lower() or DEFAULT_FEED,
        )
        selected_daily = candidate_daily_frames[str(best_single["candidate_name"])].copy()
        selected_targets = candidate_targets[str(best_single["candidate_name"])].copy()
        selected_positions = candidate_positions[str(best_single["candidate_name"])].copy()
        split_metrics = _split_metrics_for_single(best_single)

        if len(promotion_pool) >= 2:
            basket_strategy, basket_daily, basket_targets, basket_positions, basket_split_metrics = _build_basket_strategy(
                promotion_pool,
                candidate_daily_frames,
                candidate_targets,
                source_run_id="",
                feed=str(args.feed).strip().lower() or DEFAULT_FEED,
            )
            basket_test = basket_split_metrics[basket_split_metrics["split"] == "test"].iloc[0]
            single_test = split_metrics[split_metrics["split"] == "test"].iloc[0]
            if (
                float(basket_test["fitness_proxy"]) > float(single_test["fitness_proxy"])
                and float(basket_test["sharpe_proxy"]) > float(single_test["sharpe_proxy"])
            ):
                selected_strategy = basket_strategy
                selected_daily = basket_daily
                selected_targets = basket_targets
                selected_positions = basket_positions
                split_metrics = basket_split_metrics
                promotion_notes.append("Basket selected because unseen fitness and Sharpe both exceeded the best single candidate.")

    run_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    selected_strategy.source_run_id = run_stamp
    selected_strategy.promotion_profile = str(args.promotion_profile or DEFAULT_PROMOTION_PROFILE)
    if promotion_notes:
        selected_strategy.notes.extend(promotion_notes)

    iex_robustness = _run_selected_iex_robustness(
        cfg=cfg,
        broker=broker,
        end_date=end_date,
        classification_snapshot_date=resolved["classification_snapshot_date"],
        strategy=selected_strategy,
        train_days=len(prepared["splits"].train_dates),
        oos_days=len(prepared["splits"].oos_dates),
        test_days=len(prepared["splits"].test_dates),
        min_universe=baseline.min_universe,
        min_universe_ratio=baseline.min_universe_ratio,
    )

    promotion_lines = [
        "# Promotion Report",
        "",
        f"- baseline_id: {baseline.baseline_id}",
        f"- feed: {resolved['feed']}",
        f"- approved: {int(selected_strategy.approved)}",
        f"- strategy_type: {selected_strategy.strategy_type}",
        f"- member_count: {len(selected_strategy.members)}",
        f"- degraded_depth: {int(prepared['degraded_depth'])}",
        f"- universe_coverage_ratio: {prepared['coverage_ratio']:.3f}",
    ]
    for note in selected_strategy.notes:
        promotion_lines.append(f"- note: {note}")
    promotion_report = "\n".join(promotion_lines) + "\n"

    outputs = {
        "family_leaderboard": family_leaderboard,
        "candidate_leaderboard": candidate_leaderboard,
        "oos_survivors": oos_survivors,
        "unseen_results": promotion_pool if not promotion_pool.empty else unseen_passers,
        "candidate_correlation": candidate_correlation,
        "split_metrics": split_metrics,
        "selected_strategy_daily_equity": selected_daily,
        "selected_strategy_daily_positions": selected_positions,
        "selected_strategy_daily_targets": selected_targets,
        "selected_strategy": selected_strategy,
        "promotion_report": promotion_report,
        "iex_robustness": iex_robustness,
        "metadata": {
            "baseline_id": baseline.baseline_id,
            "baseline_file": str(cfg.research_baseline_file),
            "feed": resolved["feed"],
            "latest_completed_date": prepared["splits"].latest_completed_date.isoformat(),
            "usable_end_date": prepared["splits"].usable_end_date.isoformat(),
            "classification_snapshot": str(prepared["classification_snapshot_path"]),
            "classification_snapshot_date": prepared["classification_snapshot_date"],
            "train_days": len(prepared["splits"].train_dates),
            "oos_days": len(prepared["splits"].oos_dates),
            "test_days": len(prepared["splits"].test_dates),
            "round_trip_cost_bps": cfg.round_trip_cost_bps,
            "gross_exposure": selected_strategy.gross_exposure,
            "book_mode": selected_strategy.book_mode,
            "alpha_set": resolved["alpha_set"],
            "group_level_grid": group_level_grid,
            "book_mode_grid": book_mode_grid,
            "top_n_grid": top_n_grid,
            "decay_grid": decay_grid,
            "truncation_grid": truncation_grid,
            "degraded_depth": prepared["degraded_depth"],
            "coverage_ratio": prepared["coverage_ratio"],
            "dynamic_baseline": resolved["dynamic_baseline"],
            "approximations": [
                "Sector and industry classifications are snapshot-based, not point-in-time historical classifications.",
                "Historical shortability is approximated after the liquidity/universe screen.",
                "Execution is modeled as next-day open to next-day open with daily bars.",
                "Free SIP/IEX historical access may lag the live paper environment.",
            ],
        },
    }

    run_dir = write_research_outputs(
        outputs,
        research_runs_dir=cfg.research_runs_dir,
        run_stamp=run_stamp,
    )
    if selected_strategy.approved and not args.no_promote:
        write_strategy_spec(selected_strategy_path(cfg.private_dir), selected_strategy)

    print(f"research_dir: {run_dir}")
    print(f"approved: {int(selected_strategy.approved)}")
    print(f"strategy_type: {selected_strategy.strategy_type}")
    print(f"member_count: {len(selected_strategy.members)}")
    print("split_metrics:")
    print(split_metrics.to_string(index=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the staged Alpaca research sweep and optionally promote the selected strategy."
    )
    parser.add_argument("--baseline-file", default="", help="Optional research baseline JSON file.")
    parser.add_argument(
        "--dynamic-baseline",
        action="store_true",
        help="Ignore the pinned classification snapshot date and use the latest eligible snapshot for the chosen end date.",
    )
    parser.add_argument("--end-date", default="", help="Override latest completed date in YYYY-MM-DD.")
    parser.add_argument("--feed", default="", help="Override historical feed for research runs.")
    parser.add_argument("--train-days", type=int, default=0, help="Override train execution-day count.")
    parser.add_argument("--oos-days", type=int, default=0, help="Override OOS execution-day count.")
    parser.add_argument("--test-days", type=int, default=0, help="Override unseen execution-day count.")
    parser.add_argument("--alpha-set", default="", help="Alpha set name, family, or comma-separated list.")
    parser.add_argument("--group-level-grid", default="", help="Comma-separated group levels.")
    parser.add_argument("--book-mode-grid", default="", help="Comma-separated book modes.")
    parser.add_argument("--top-n-grid", default="", help="Comma-separated top-N values.")
    parser.add_argument("--decay-grid", default="", help="Comma-separated signal decay windows.")
    parser.add_argument("--truncation-grid", default="", help="Comma-separated truncation values, use none to disable.")
    parser.add_argument("--max-candidates-per-family", type=int, default=DEFAULT_MAX_PER_FAMILY)
    parser.add_argument("--promotion-profile", default=DEFAULT_PROMOTION_PROFILE)
    parser.add_argument(
        "--no-promote",
        action="store_true",
        help="Do not copy an approved strategy to paper/alpaca/private/selected_strategy.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(run_research(args))


if __name__ == "__main__":
    raise SystemExit(main())
