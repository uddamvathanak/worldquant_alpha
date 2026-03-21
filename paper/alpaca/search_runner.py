from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any

import pandas as pd

from alpha_dsl import candidate_signature
from alpha_registry import StrategyMember, StrategySpec, get_alpha_registry, write_strategy_spec
from backtest_engine import ResearchCandidate, run_research_candidate, summarize_research_candidate
from broker_alpaca import AlpacaBroker
from config import load_config, parse_trade_date
from free_model_generator import FreeModelGeneratorError, generate_mutation_candidates, generator_enabled
from research_cache import ResearchCache, build_evaluation_key
from research_runner import (
    DEFAULT_FEED,
    DEFAULT_PROMOTION_PROFILE,
    _apply_sector_vs_none_rule,
    _filter_oos_survivors,
    _filter_unseen_passers,
    _prepare_inputs,
)


SCREEN_ALPHA_SET = ",".join(
    [
        "smooth_momentum",
        "breakout_quality",
        "vwap_gap_revert",
        "vwap_extreme_revert",
        "pv_corr_contra",
        "momentum_with_volume_confirm",
        "profit_asset_gate_proxy_v1",
    ]
)
SCREEN_TRAIN_DAYS = 252
SCREEN_OOS_DAYS = 63
SCREEN_TEST_DAYS = 63
VALIDATION_TRAIN_DAYS = 1008
VALIDATION_OOS_DAYS = 252
VALIDATION_TEST_DAYS = 252
SCREEN_GROUP_LEVELS = ["sector"]
SCREEN_BOOK_MODES = ["sector"]
SCREEN_TOP_N = [50]
SCREEN_DECAY = [0]
SCREEN_TRUNCATION = [None]
STABILITY_GROUP_LEVELS = ["sector", "industry"]
STABILITY_TOP_N = [30, 50, 75]
STABILITY_DECAY = [0, 3, 5]
STABILITY_TRUNCATION = [None, 0.05]
STAGES = ["registry_screen", "stability_expand", "full_validation", "mutation"]
RESULT_FILES = {
    "registry_screen": "screen_results.csv",
    "stability_expand": "stability_expand_results.csv",
    "full_validation": "full_validation_results.csv",
    "mutation": "mutation_results.csv",
}
GLOBAL_LOCK_FILE = "alpha_search.lock"
MAX_MUTATION_CANDIDATES = 20


class SearchRunnerError(RuntimeError):
    pass


@dataclass(slots=True)
class SearchContext:
    cfg: Any
    run_id: str
    run_dir: Path
    state_path: Path
    queue_path: Path
    report_path: Path
    summary_path: Path
    errors_path: Path
    survivor_path: Path
    shadow_strategy_path: Path
    global_shadow_path: Path
    research_cache: ResearchCache
    batch_size: int
    deadline_monotonic: float
    global_lock_path: Path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _stage_complete_name(stage: str) -> str:
    return f"{stage}_complete"


def _stage_running_name(stage: str) -> str:
    return f"{stage}_running"


def _stage_pending_name(stage: str) -> str:
    return f"{stage}_pending"


def _stage_candidate_id(stage: str, candidate_name: str) -> str:
    return f"{stage}:{candidate_name}"


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        rows.append(json.loads(text))
    return rows


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    serialized = dict(row)
    serialized["params_json"] = json.dumps(serialized.pop("params", {}), sort_keys=True)
    serialized["parent_candidates_json"] = json.dumps(
        serialized.pop("parent_candidates", []),
        sort_keys=True,
    )
    return serialized


def _deserialize_results(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        frame = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    if frame.empty:
        return frame
    if "params_json" in frame.columns:
        frame["params"] = frame["params_json"].fillna("{}").map(lambda text: json.loads(str(text)))
    else:
        frame["params"] = [{} for _ in range(len(frame))]
    if "parent_candidates_json" in frame.columns:
        frame["parent_candidates"] = frame["parent_candidates_json"].fillna("[]").map(lambda text: json.loads(str(text)))
    else:
        frame["parent_candidates"] = [[] for _ in range(len(frame))]
    return frame


def _candidate_cache_signature(record: dict[str, Any]) -> str:
    return candidate_signature(
        {
            "template_name": str(record["alpha_name"]),
            "family": str(record["family"]),
            "params": dict(record.get("params", {})),
            "group_level": str(record["group_level"]),
            "book_mode": str(record["book_mode"]),
            "top_n": int(record["top_n"]),
            "signal_decay": int(record.get("signal_decay", 0)),
            "score_truncation": record.get("score_truncation", None),
            "source": "cache",
            "parent_candidates": [],
            "notes": "",
        }
    )


def _append_result_row(path: Path, row: dict[str, Any]) -> None:
    serialized = _serialize_row(row)
    frame = pd.DataFrame([serialized])
    frame.to_csv(path, mode="a", header=not path.exists(), index=False)


def _ensure_results_file(path: Path, *, columns: list[str]) -> None:
    if path.exists():
        return
    pd.DataFrame(columns=columns).to_csv(path, index=False)


def _sort_candidates(frame: pd.DataFrame, *, prefix: str = "test_") -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    columns = [
        f"{prefix}fitness_proxy",
        f"{prefix}sharpe_proxy",
        f"{prefix}returns",
        f"{prefix}max_drawdown",
        f"{prefix}turnover_mean",
    ]
    available = [column for column in columns if column in frame.columns]
    if len(available) != len(columns):
        return frame.copy().reset_index(drop=True)
    return frame.sort_values(
        columns,
        ascending=[False, False, False, True, True],
    ).reset_index(drop=True)


def _prefix_summary(summary: dict[str, Any], prefix: str) -> dict[str, Any]:
    exclude = {
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
    return {f"{prefix}{key}": value for key, value in summary.items() if key not in exclude}


def _build_split_map(prepared: dict[str, Any]) -> dict[str, str]:
    return {
        execution_date.isoformat(): name
        for name, dates in [
            ("train", prepared["splits"].train_dates),
            ("oos", prepared["splits"].oos_dates),
            ("test", prepared["splits"].test_dates),
        ]
        for execution_date in dates
    }


def _prepare_stage_inputs(
    *,
    cfg: Any,
    broker: AlpacaBroker,
    end_date: date,
    feed: str,
    train_days: int,
    oos_days: int,
    test_days: int,
) -> dict[str, Any]:
    prepared = _prepare_inputs(
        cfg=cfg,
        broker=broker,
        end_date=end_date,
        feed=feed,
        train_days=train_days,
        oos_days=oos_days,
        test_days=test_days,
    )
    prepared["split_map"] = _build_split_map(prepared)
    return prepared


def _queue_record_from_candidate(
    stage: str,
    candidate: ResearchCandidate,
    *,
    source: str,
    parent_candidates: list[str] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "stage": stage,
        "candidate_id": _stage_candidate_id(stage, candidate.name),
        "candidate_name": candidate.name,
        "alpha_name": candidate.alpha_name,
        "family": candidate.family,
        "params": dict(candidate.params),
        "group_level": candidate.group_level,
        "book_mode": candidate.book_mode,
        "top_n": candidate.top_n,
        "gross_exposure": candidate.gross_exposure,
        "signal_decay": candidate.signal_decay,
        "score_truncation": candidate.score_truncation,
        "source": source,
        "parent_candidates": list(parent_candidates or []),
        "notes": notes,
    }


def _candidate_from_record(record: dict[str, Any]) -> ResearchCandidate:
    return ResearchCandidate(
        alpha_name=str(record["alpha_name"]),
        family=str(record["family"]),
        params=dict(record.get("params", {})),
        group_level=str(record["group_level"]),
        book_mode=str(record["book_mode"]),
        top_n=int(record["top_n"]),
        gross_exposure=float(record["gross_exposure"]),
        signal_decay=int(record.get("signal_decay", 0)),
        score_truncation=(
            None
            if record.get("score_truncation", None) in {"", None}
            else float(record["score_truncation"])
        ),
    )


def _evaluate_candidate_record(
    *,
    prepared: dict[str, Any],
    round_trip_cost_bps: float,
    record: dict[str, Any],
) -> dict[str, Any]:
    candidate = _candidate_from_record(record)
    daily, targets, _ = run_research_candidate(
        prepared["bars"],
        prepared["classifications"],
        prepared["universe_lookup"],
        prepared["execution_map"],
        candidate,
        round_trip_cost_bps=round_trip_cost_bps,
    )
    daily["split"] = daily["execution_date"].map(prepared["split_map"])
    if not targets.empty:
        targets["split"] = targets["execution_date"].map(prepared["split_map"])

    train_summary = summarize_research_candidate(
        daily[daily["split"] == "train"].copy(),
        targets[targets["split"] == "train"].copy(),
        candidate,
    )
    oos_summary = summarize_research_candidate(
        daily[daily["split"] == "oos"].copy(),
        targets[targets["split"] == "oos"].copy(),
        candidate,
    )
    test_summary = summarize_research_candidate(
        daily[daily["split"] == "test"].copy(),
        targets[targets["split"] == "test"].copy(),
        candidate,
    )
    row = {
        **candidate.to_dict(),
        "stage": str(record["stage"]),
        "candidate_id": str(record["candidate_id"]),
        "source": str(record.get("source", "registry")),
        "parent_candidates": list(record.get("parent_candidates", [])),
        "notes": str(record.get("notes", "")),
        **_prefix_summary(train_summary, "train_"),
        **_prefix_summary(oos_summary, "oos_"),
        **_prefix_summary(test_summary, "test_"),
    }
    return row


def _latest_run_id(search_runs_dir: Path, *, exclude: str | None = None) -> str | None:
    if not search_runs_dir.exists():
        return None
    run_dirs = [path for path in search_runs_dir.iterdir() if path.is_dir()]
    if exclude:
        run_dirs = [path for path in run_dirs if path.name != exclude]
    if not run_dirs:
        return None
    return sorted(run_dirs, key=lambda item: item.name)[-1].name


def _base_state(run_id: str, *, args: argparse.Namespace, cfg: Any) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at": _utc_now_iso(),
        "current_stage": _stage_pending_name("mutation" if args.mutation_only else "registry_screen"),
        "completed_stages": [],
        "pending_candidates": [],
        "completed_candidates": [],
        "failed_candidates": [],
        "current_activity": "idle",
        "active_candidate_id": "",
        "active_candidate_name": "",
        "last_activity_at": _utc_now_iso(),
        "best_shadow_candidate": None,
        "last_checkpoint_at": _utc_now_iso(),
        "config": {
            "feed": str(args.feed).strip().lower() or DEFAULT_FEED,
            "end_date": str(args.end_date or ""),
            "screen_only": bool(args.screen_only),
            "mutation_only": bool(args.mutation_only),
            "promotion_profile": str(args.promotion_profile or DEFAULT_PROMOTION_PROFILE),
            "batch_size": int(args.batch_size or cfg.alpha_search_batch_size),
            "max_runtime_min": int(args.max_runtime_min or cfg.alpha_search_max_runtime_min),
            "alpha_set": SCREEN_ALPHA_SET,
            "gross_exposure": cfg.gross_exposure,
            "shadow_only": True,
        },
        "notes": [],
    }


def _active_stage_sequence(args: argparse.Namespace) -> list[str]:
    if args.screen_only:
        return ["registry_screen"]
    if args.mutation_only:
        return ["mutation"]
    return list(STAGES)


def _determine_start_stage(state: dict[str, Any], *, args: argparse.Namespace) -> str:
    if args.stage != "auto":
        return str(args.stage)
    current = str(state.get("current_stage", ""))
    if current in {"shadow_finalized", ""} or current.endswith("_complete"):
        sequence = _active_stage_sequence(args)
        if not sequence:
            raise SearchRunnerError("No active stages configured for search run.")
        for stage in sequence:
            if _stage_complete_name(stage) not in state.get("completed_stages", []):
                return stage
        return sequence[-1]
    if current.endswith("_running") or current.endswith("_pending"):
        return current.rsplit("_", 1)[0]
    return current


def _global_lock_owned(lock_path: Path) -> bool:
    if not lock_path.exists():
        return False
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return True
    pid = int(payload.get("pid", 0) or 0)
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_global_lock(lock_path: Path, *, run_id: str) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if _global_lock_owned(lock_path):
            return False
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"pid": os.getpid(), "run_id": run_id, "created_at": _utc_now_iso()}, handle)
    return True


def _release_global_lock(lock_path: Path) -> None:
    try:
        if lock_path.exists():
            lock_path.unlink()
    except FileNotFoundError:
        pass


def _stage_results_path(ctx: SearchContext, stage: str) -> Path:
    return ctx.run_dir / RESULT_FILES[stage]


def _set_state_checkpoint(state: dict[str, Any], ctx: SearchContext) -> None:
    state["last_checkpoint_at"] = _utc_now_iso()
    _write_json(ctx.state_path, state)


def _set_activity(
    state: dict[str, Any],
    ctx: SearchContext,
    *,
    activity: str,
    active_candidate_id: str = "",
    active_candidate_name: str = "",
) -> None:
    state["current_activity"] = str(activity)
    state["active_candidate_id"] = str(active_candidate_id)
    state["active_candidate_name"] = str(active_candidate_name)
    state["last_activity_at"] = _utc_now_iso()
    _set_state_checkpoint(state, ctx)


def _clear_activity(state: dict[str, Any], ctx: SearchContext) -> None:
    _set_activity(
        state,
        ctx,
        activity="idle",
        active_candidate_id="",
        active_candidate_name="",
    )


def _log(message: str) -> None:
    print(message, flush=True)


def _stage_a_candidates(gross_exposure: float) -> list[dict[str, Any]]:
    from backtest_engine import expand_research_candidates

    candidates = expand_research_candidates(
        alpha_set=SCREEN_ALPHA_SET,
        group_level_grid=SCREEN_GROUP_LEVELS,
        book_mode_grid=SCREEN_BOOK_MODES,
        top_n_grid=SCREEN_TOP_N,
        decay_grid=SCREEN_DECAY,
        truncation_grid=SCREEN_TRUNCATION,
        gross_exposure=float(gross_exposure),
    )
    return [_queue_record_from_candidate("registry_screen", candidate, source="registry_screen") for candidate in candidates]


def _row_to_candidate(
    row: pd.Series,
    *,
    stage: str,
    gross_exposure: float,
    source: str,
    parent_candidates: list[str] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    candidate = ResearchCandidate(
        alpha_name=str(row["alpha_name"]),
        family=str(row["family"]),
        params=dict(row["params"]),
        group_level=str(row["group_level"]),
        book_mode=str(row["book_mode"]),
        top_n=int(row["top_n"]),
        gross_exposure=float(gross_exposure),
        signal_decay=int(row.get("signal_decay", 0)),
        score_truncation=(
            None
            if row.get("score_truncation", None) in {"", None} or pd.isna(row.get("score_truncation"))
            else float(row["score_truncation"])
        ),
    )
    return _queue_record_from_candidate(
        stage,
        candidate,
        source=source,
        parent_candidates=parent_candidates,
        notes=notes,
    )


def _stage_b_candidates(ctx: SearchContext, *, gross_exposure: float) -> list[dict[str, Any]]:
    screen_results = _deserialize_results(_stage_results_path(ctx, "registry_screen"))
    survivors = _filter_oos_survivors(screen_results)
    survivors = _sort_candidates(survivors, prefix="test_")
    survivors = survivors.groupby("family", as_index=False).head(2).reset_index(drop=True)
    survivors.to_csv(ctx.survivor_path, index=False)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, row in survivors.iterrows():
        for group_level in STABILITY_GROUP_LEVELS:
            for top_n in STABILITY_TOP_N:
                for signal_decay in STABILITY_DECAY:
                    for truncation in STABILITY_TRUNCATION:
                        candidate = ResearchCandidate(
                            alpha_name=str(row["alpha_name"]),
                            family=str(row["family"]),
                            params=dict(row["params"]),
                            group_level=group_level,
                            book_mode="sector",
                            top_n=int(top_n),
                            gross_exposure=float(gross_exposure),
                            signal_decay=int(signal_decay),
                            score_truncation=truncation,
                        )
                        if candidate.name in seen:
                            continue
                        seen.add(candidate.name)
                        records.append(
                            _queue_record_from_candidate(
                                "stability_expand",
                                candidate,
                                source="stability_expand",
                                parent_candidates=[str(row["candidate_name"])],
                            )
                        )
    return records


def _stage_c_candidates(ctx: SearchContext, *, gross_exposure: float) -> list[dict[str, Any]]:
    stability_results = _deserialize_results(_stage_results_path(ctx, "stability_expand"))
    filtered = _filter_oos_survivors(stability_results)
    filtered = filtered[filtered["test_positive_month_ratio"] >= 0.55].copy().reset_index(drop=True)
    filtered = _sort_candidates(filtered, prefix="test_").head(5).reset_index(drop=True)
    filtered.to_csv(ctx.survivor_path, index=False)
    return [
        _row_to_candidate(
            row,
            stage="full_validation",
            gross_exposure=gross_exposure,
            source="full_validation",
            parent_candidates=list(row.get("parent_candidates", [])),
        )
        for _, row in filtered.iterrows()
    ]


def _seed_rows_from_previous_run(search_runs_dir: Path, *, exclude_run_id: str) -> pd.DataFrame:
    latest = _latest_run_id(search_runs_dir, exclude=exclude_run_id)
    if latest is None:
        raise SearchRunnerError("No prior search run is available for mutation-only seeding.")
    path = search_runs_dir / latest / RESULT_FILES["full_validation"]
    frame = _deserialize_results(path)
    if frame.empty:
        raise SearchRunnerError("Latest prior search run has no full validation results for mutation seeding.")
    return _sort_candidates(frame, prefix="test_").head(3).reset_index(drop=True)


def _mutation_seed_rows(ctx: SearchContext, *, mutation_only: bool) -> pd.DataFrame:
    current = _deserialize_results(_stage_results_path(ctx, "full_validation"))
    if not current.empty:
        return _sort_candidates(current, prefix="test_").head(3).reset_index(drop=True)
    if mutation_only:
        return _seed_rows_from_previous_run(ctx.cfg.search_runs_dir, exclude_run_id=ctx.run_id)
    return pd.DataFrame()


def _family_context() -> dict[str, list[str]]:
    registry = get_alpha_registry()
    family_map: dict[str, list[str]] = {}
    for definition in registry.values():
        family_map.setdefault(definition.family, []).append(definition.name)
    return {key: sorted(value) for key, value in sorted(family_map.items())}


def _mutation_candidates(ctx: SearchContext, *, gross_exposure: float, mutation_only: bool) -> tuple[list[dict[str, Any]], str | None]:
    if not generator_enabled():
        return [], "generator_disabled"

    seed_rows = _mutation_seed_rows(ctx, mutation_only=mutation_only)
    if seed_rows.empty:
        return [], "no_mutation_seeds"

    seed_candidates = []
    for _, row in seed_rows.iterrows():
        seed_candidates.append(
            {
                "candidate_name": str(row["candidate_name"]),
                "alpha_name": str(row["alpha_name"]),
                "family": str(row["family"]),
                "params": dict(row["params"]),
                "group_level": str(row["group_level"]),
                "book_mode": str(row["book_mode"]),
                "top_n": int(row["top_n"]),
                "signal_decay": int(row.get("signal_decay", 0)),
                "score_truncation": (
                    None
                    if row.get("score_truncation", None) in {"", None} or pd.isna(row.get("score_truncation"))
                    else float(row["score_truncation"])
                ),
                "test_returns": float(row.get("test_returns", 0.0)),
                "test_fitness_proxy": float(row.get("test_fitness_proxy", 0.0)),
                "test_sharpe_proxy": float(row.get("test_sharpe_proxy", 0.0)),
                "test_max_drawdown": float(row.get("test_max_drawdown", 0.0)),
            }
        )

    try:
        generated, _ = generate_mutation_candidates(
            seed_candidates=seed_candidates,
            family_context=_family_context(),
            max_candidates=MAX_MUTATION_CANDIDATES,
        )
    except FreeModelGeneratorError as exc:
        return [], str(exc)

    existing_names = set(_deserialize_results(_stage_results_path(ctx, "full_validation"))["candidate_name"].tolist())
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate_spec in generated:
        candidate = ResearchCandidate(
            alpha_name=candidate_spec.template_name,
            family=candidate_spec.family,
            params=dict(candidate_spec.params),
            group_level=candidate_spec.group_level,
            book_mode=candidate_spec.book_mode,
            top_n=candidate_spec.top_n,
            gross_exposure=float(gross_exposure),
            signal_decay=candidate_spec.signal_decay,
            score_truncation=candidate_spec.score_truncation,
        )
        if candidate.name in existing_names:
            continue
        signature = candidate_signature(candidate_spec.to_dict())
        if signature in seen:
            continue
        seen.add(signature)
        records.append(
            _queue_record_from_candidate(
                "mutation",
                candidate,
                source=candidate_spec.source,
                parent_candidates=candidate_spec.parent_candidates,
                notes=candidate_spec.notes,
            )
        )
    return records, None


def _stage_queue_records(
    ctx: SearchContext,
    stage: str,
    *,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    if stage == "registry_screen":
        return _stage_a_candidates(ctx.cfg.gross_exposure)
    if stage == "stability_expand":
        return _stage_b_candidates(ctx, gross_exposure=ctx.cfg.gross_exposure)
    if stage == "full_validation":
        return _stage_c_candidates(ctx, gross_exposure=ctx.cfg.gross_exposure)
    if stage == "mutation":
        records, note = _mutation_candidates(
            ctx,
            gross_exposure=ctx.cfg.gross_exposure,
            mutation_only=bool(state["config"].get("mutation_only", False)),
        )
        if note:
            notes = set(state.get("notes", []))
            notes.add(note)
            state["notes"] = sorted(notes)
        return records
    raise SearchRunnerError(f"Unsupported search stage: {stage}")


def _sync_stage_queue(ctx: SearchContext, *, stage: str, state: dict[str, Any]) -> list[dict[str, Any]]:
    current_queue = _read_jsonl(ctx.queue_path)
    if current_queue and all(str(item.get("stage", "")) == stage for item in current_queue):
        return current_queue
    records = _stage_queue_records(ctx, stage, state=state)
    _write_jsonl(ctx.queue_path, records)
    state["pending_candidates"] = [str(record["candidate_id"]) for record in records]
    state["last_checkpoint_at"] = _utc_now_iso()
    _write_json(ctx.state_path, state)
    return records


def _completed_ids_from_results(path: Path) -> set[str]:
    frame = _deserialize_results(path)
    if frame.empty or "candidate_id" not in frame.columns:
        return set()
    return set(frame["candidate_id"].astype(str).tolist())


def _process_stage(
    ctx: SearchContext,
    *,
    stage: str,
    prepared: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    state["current_stage"] = _stage_running_name(stage)
    queue_records = _sync_stage_queue(ctx, stage=stage, state=state)
    results_path = _stage_results_path(ctx, stage)
    evaluation_key = build_evaluation_key(
        feed=str(state["config"].get("feed", DEFAULT_FEED)),
        latest_completed_date=prepared["splits"].latest_completed_date.isoformat(),
        train_days=len(prepared["splits"].train_dates),
        oos_days=len(prepared["splits"].oos_dates),
        test_days=len(prepared["splits"].test_dates),
        classification_snapshot=str(prepared["classification_snapshot_path"]),
        round_trip_cost_bps=ctx.cfg.round_trip_cost_bps,
    )

    completed_ids = set(state.get("completed_candidates", [])) | _completed_ids_from_results(results_path)
    failed_ids = set(state.get("failed_candidates", []))
    pending_records = [
        record
        for record in queue_records
        if str(record["candidate_id"]) not in completed_ids
        and str(record["candidate_id"]) not in failed_ids
    ]
    state["pending_candidates"] = [str(record["candidate_id"]) for record in pending_records]
    _set_activity(state, ctx, activity=f"{stage}:ready")

    processed = 0
    for record in pending_records:
        if processed >= int(ctx.batch_size) or time.monotonic() >= ctx.deadline_monotonic:
            state["current_stage"] = _stage_running_name(stage)
            _set_activity(state, ctx, activity=f"{stage}:paused")
            return False
        _log(
            f"stage={stage} action=evaluate candidate={record['candidate_name']} "
            f"processed={processed} remaining={len(pending_records) - processed}"
        )
        _set_activity(
            state,
            ctx,
            activity=f"{stage}:evaluating",
            active_candidate_id=str(record["candidate_id"]),
            active_candidate_name=str(record["candidate_name"]),
        )
        try:
            cache_signature = _candidate_cache_signature(record)
            row = ctx.research_cache.get(
                evaluation_key=evaluation_key,
                candidate_signature=cache_signature,
            )
            if row is None:
                row = _evaluate_candidate_record(
                    prepared=prepared,
                    round_trip_cost_bps=ctx.cfg.round_trip_cost_bps,
                    record=record,
                )
                ctx.research_cache.put(
                    evaluation_key=evaluation_key,
                    candidate_signature=cache_signature,
                    candidate_name=str(record["candidate_name"]),
                    result=row,
                )
                row = dict(row)
                row["cache_hit"] = 0
            else:
                row = dict(row)
                row["cache_hit"] = 1
            row["stage"] = str(record["stage"])
            row["candidate_id"] = str(record["candidate_id"])
            row["candidate_name"] = str(record["candidate_name"])
            row["source"] = str(record.get("source", row.get("source", "registry")))
            row["parent_candidates"] = list(record.get("parent_candidates", row.get("parent_candidates", [])))
            row["notes"] = str(record.get("notes", row.get("notes", "")))
            _append_result_row(results_path, row)
            completed_ids.add(str(record["candidate_id"]))
            _log(
                f"stage={stage} action=complete candidate={record['candidate_name']} "
                f"cache_hit={int(row.get('cache_hit', 0))}"
            )
        except Exception as exc:
            failed_ids.add(str(record["candidate_id"]))
            _log(
                f"stage={stage} action=failed candidate={record['candidate_name']} error={exc}"
            )
            _append_jsonl(
                ctx.errors_path,
                {
                    "stage": stage,
                    "candidate_id": str(record["candidate_id"]),
                    "candidate_name": str(record["candidate_name"]),
                    "error": str(exc),
                    "at": _utc_now_iso(),
                },
            )
        processed += 1
        state["completed_candidates"] = sorted(completed_ids)
        state["failed_candidates"] = sorted(failed_ids)
        state["pending_candidates"] = [
            str(item["candidate_id"])
            for item in queue_records
            if str(item["candidate_id"]) not in completed_ids and str(item["candidate_id"]) not in failed_ids
        ]
        _set_activity(state, ctx, activity=f"{stage}:checkpoint")

    state["current_stage"] = _stage_complete_name(stage)
    completed_stages = set(state.get("completed_stages", []))
    completed_stages.add(_stage_complete_name(stage))
    state["completed_stages"] = sorted(completed_stages)
    state["pending_candidates"] = []
    _set_activity(state, ctx, activity=f"{stage}:complete")
    return True


def _build_shadow_strategy_from_row(
    row: pd.Series,
    *,
    feed: str,
    source_run_id: str,
    promotion_profile: str,
) -> StrategySpec:
    member = StrategyMember(
        name=str(row["candidate_name"]),
        alpha_name=str(row["alpha_name"]),
        family=str(row["family"]),
        weight=1.0,
        params=dict(row["params"]),
        group_level=str(row["group_level"]),
        book_mode=str(row["book_mode"]),
        top_n=int(row["top_n"]),
        signal_decay=int(row.get("signal_decay", 0)),
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
        approved=False,
        source_run_id=source_run_id,
        promotion_profile=promotion_profile,
        notes=["Shadow-only search artifact. Runtime promotion requires an explicit later step."],
    )


def _unseen_passer_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return _filter_unseen_passers(_filter_oos_survivors(frame))


def _choose_best_shadow_candidate(ctx: SearchContext) -> tuple[pd.Series | None, str | None]:
    for stage in ["mutation", "full_validation", "stability_expand", "registry_screen"]:
        frame = _deserialize_results(_stage_results_path(ctx, stage))
        if frame.empty:
            continue
        promotion_pool = _apply_sector_vs_none_rule(_unseen_passer_frame(frame))
        ranked = _sort_candidates(promotion_pool if not promotion_pool.empty else frame, prefix="test_")
        if not ranked.empty:
            return ranked.iloc[0], stage
    return None, None


def _write_shadow_artifacts(ctx: SearchContext, *, state: dict[str, Any]) -> None:
    row, stage = _choose_best_shadow_candidate(ctx)
    winner_summary: dict[str, Any]
    if row is None or stage is None:
        winner_summary = {
            "approved": False,
            "source_run_id": ctx.run_id,
            "reason": "No evaluated candidates were available to form a shadow strategy.",
        }
        _write_json(ctx.summary_path, winner_summary)
        state["best_shadow_candidate"] = None
        return

    strategy = _build_shadow_strategy_from_row(
        row,
        feed=str(state["config"].get("feed", DEFAULT_FEED)),
        source_run_id=ctx.run_id,
        promotion_profile=str(state["config"].get("promotion_profile", DEFAULT_PROMOTION_PROFILE)),
    )
    write_strategy_spec(ctx.shadow_strategy_path, strategy)
    write_strategy_spec(ctx.global_shadow_path, strategy)

    validation_rows = _deserialize_results(_stage_results_path(ctx, "full_validation"))
    mutation_rows = _deserialize_results(_stage_results_path(ctx, "mutation"))
    unseen_passed = not _unseen_passer_frame(pd.concat([validation_rows, mutation_rows], ignore_index=True)).empty
    winner_summary = {
        "approved": False,
        "source_run_id": ctx.run_id,
        "shadow_stage": stage,
        "candidate_name": str(row["candidate_name"]),
        "alpha_name": str(row["alpha_name"]),
        "family": str(row["family"]),
        "group_level": str(row["group_level"]),
        "book_mode": str(row["book_mode"]),
        "top_n": int(row["top_n"]),
        "signal_decay": int(row.get("signal_decay", 0)),
        "score_truncation": (
            None
            if row.get("score_truncation", None) in {"", None} or pd.isna(row.get("score_truncation"))
            else float(row["score_truncation"])
        ),
        "params": dict(row["params"]),
        "test_returns": float(row.get("test_returns", 0.0)),
        "test_fitness_proxy": float(row.get("test_fitness_proxy", 0.0)),
        "test_sharpe_proxy": float(row.get("test_sharpe_proxy", 0.0)),
        "test_max_drawdown": float(row.get("test_max_drawdown", 0.0)),
        "test_turnover_mean": float(row.get("test_turnover_mean", 0.0)),
        "test_positive_month_ratio": float(row.get("test_positive_month_ratio", 0.0)),
        "days_with_full_book_ratio": float(row.get("test_days_with_full_book_ratio", 0.0)),
        "unseen_bar_passed": bool(unseen_passed),
        "reason": (
            "Nightly search is configured as shadow-only; selected_strategy.json remains untouched."
            if unseen_passed
            else "No candidate has passed the unseen promotion bar yet; best shadow candidate retained."
        ),
    }
    _write_json(ctx.summary_path, winner_summary)
    state["best_shadow_candidate"] = winner_summary


def _stage_count(path: Path) -> int:
    frame = _deserialize_results(path)
    return int(len(frame))


def _best_stage_candidate(path: Path) -> dict[str, Any] | None:
    frame = _deserialize_results(path)
    if frame.empty:
        return None
    ranked = _sort_candidates(frame, prefix="test_")
    if ranked.empty:
        return None
    row = ranked.iloc[0]
    return {
        "candidate_name": str(row["candidate_name"]),
        "alpha_name": str(row["alpha_name"]),
        "family": str(row["family"]),
        "test_returns": float(row.get("test_returns", 0.0)),
        "test_fitness_proxy": float(row.get("test_fitness_proxy", 0.0)),
        "test_sharpe_proxy": float(row.get("test_sharpe_proxy", 0.0)),
    }


def _write_search_report(ctx: SearchContext, *, state: dict[str, Any]) -> None:
    report_lines = [
        "# Search Report",
        "",
        f"- run_id: {ctx.run_id}",
        f"- generated_at_utc: {_utc_now_iso()}",
        f"- current_stage: {state.get('current_stage', '')}",
        f"- current_activity: {state.get('current_activity', '') or 'unknown'}",
        f"- active_candidate: {state.get('active_candidate_name', '') or 'none'}",
        f"- last_activity_at: {state.get('last_activity_at', '') or 'unknown'}",
        f"- completed_stages: {', '.join(state.get('completed_stages', [])) or 'none'}",
        f"- pending_candidates: {len(state.get('pending_candidates', []))}",
        f"- completed_candidates: {len(state.get('completed_candidates', []))}",
        f"- failed_candidates: {len(state.get('failed_candidates', []))}",
        f"- notes: {', '.join(state.get('notes', [])) or 'none'}",
        "",
        "## Stage Counts",
        "",
        f"- registry_screen: {_stage_count(_stage_results_path(ctx, 'registry_screen'))}",
        f"- stability_expand: {_stage_count(_stage_results_path(ctx, 'stability_expand'))}",
        f"- full_validation: {_stage_count(_stage_results_path(ctx, 'full_validation'))}",
        f"- mutation: {_stage_count(_stage_results_path(ctx, 'mutation'))}",
        "",
        "## Best Candidate By Stage",
        "",
    ]
    for stage in STAGES:
        best = _best_stage_candidate(_stage_results_path(ctx, stage))
        if best is None:
            report_lines.append(f"- {stage}: none")
            continue
        report_lines.append(
            f"- {stage}: {best['candidate_name']} "
            f"(return={best['test_returns']:.4f}, fitness={best['test_fitness_proxy']:.4f}, "
            f"sharpe={best['test_sharpe_proxy']:.4f})"
        )

    winner_summary = _read_json(ctx.summary_path, default={})
    report_lines.extend(
        [
            "",
            "## Shadow Candidate",
            "",
        ]
    )
    if winner_summary:
        report_lines.extend(
            [
                f"- candidate: {winner_summary.get('candidate_name', 'none')}",
                f"- stage: {winner_summary.get('shadow_stage', 'n/a')}",
                f"- unseen_bar_passed: {int(bool(winner_summary.get('unseen_bar_passed', False)))}",
                f"- reason: {winner_summary.get('reason', '')}",
            ]
        )
    else:
        report_lines.append("- candidate: none")

    report_lines.extend(
        [
            "",
            "## Policy",
            "",
            "- This pipeline is shadow-only and never overwrites paper/alpaca/private/selected_strategy.json.",
        ]
    )
    ctx.report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")


def _search_context(cfg: Any, *, run_id: str, batch_size: int, max_runtime_min: int) -> SearchContext:
    run_dir = cfg.search_runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return SearchContext(
        cfg=cfg,
        run_id=run_id,
        run_dir=run_dir,
        state_path=run_dir / "search_state.json",
        queue_path=run_dir / "candidate_queue.jsonl",
        report_path=run_dir / "search_report.md",
        summary_path=run_dir / "winner_summary.json",
        errors_path=run_dir / "failed_candidates.jsonl",
        survivor_path=run_dir / "survivor_results.csv",
        shadow_strategy_path=run_dir / "shadow_strategy.json",
        global_shadow_path=cfg.shadow_strategy_file,
        research_cache=ResearchCache(cfg.research_cache_db_path),
        batch_size=int(batch_size),
        deadline_monotonic=time.monotonic() + max(1, int(max_runtime_min)) * 60.0,
        global_lock_path=cfg.private_dir / GLOBAL_LOCK_FILE,
    )


def _load_or_create_state(ctx: SearchContext, *, args: argparse.Namespace) -> dict[str, Any]:
    if args.new_run or not ctx.state_path.exists():
        state = _base_state(ctx.run_id, args=args, cfg=ctx.cfg)
        _write_json(ctx.state_path, state)
        return state
    return _read_json(ctx.state_path, default=_base_state(ctx.run_id, args=args, cfg=ctx.cfg))


def _resolve_run_id(cfg: Any, args: argparse.Namespace) -> str:
    if args.run_id:
        return str(args.run_id).strip()
    if args.new_run:
        return _run_stamp()
    latest = _latest_run_id(cfg.search_runs_dir)
    if latest:
        return latest
    return _run_stamp()


def _print_status(ctx: SearchContext, *, state: dict[str, Any]) -> int:
    winner_summary = _read_json(ctx.summary_path, default={})
    print(f"run_id: {ctx.run_id}")
    print(f"current_stage: {state.get('current_stage', '')}")
    print(f"current_activity: {state.get('current_activity', '')}")
    print(f"active_candidate_id: {state.get('active_candidate_id', '')}")
    print(f"active_candidate_name: {state.get('active_candidate_name', '')}")
    print(f"last_activity_at: {state.get('last_activity_at', '')}")
    print(f"completed_stages: {','.join(state.get('completed_stages', []))}")
    print(f"pending_candidates: {len(state.get('pending_candidates', []))}")
    print(f"completed_candidates: {len(state.get('completed_candidates', []))}")
    print(f"failed_candidates: {len(state.get('failed_candidates', []))}")
    if winner_summary:
        print(f"shadow_candidate: {winner_summary.get('candidate_name', '')}")
        print(f"shadow_stage: {winner_summary.get('shadow_stage', '')}")
        print(f"unseen_bar_passed: {int(bool(winner_summary.get('unseen_bar_passed', False)))}")
    return 0


def run_search(args: argparse.Namespace) -> int:
    cfg = load_config()
    run_id = _resolve_run_id(cfg, args)
    ctx = _search_context(
        cfg,
        run_id=run_id,
        batch_size=int(args.batch_size or cfg.alpha_search_batch_size),
        max_runtime_min=int(args.max_runtime_min or cfg.alpha_search_max_runtime_min),
    )
    state = _load_or_create_state(ctx, args=args)
    if args.status:
        return _print_status(ctx, state=state)

    if not _acquire_global_lock(ctx.global_lock_path, run_id=ctx.run_id):
        print("already_running")
        return 0

    try:
        sequence = _active_stage_sequence(args)
        start_stage = _determine_start_stage(state, args=args)
        if start_stage not in sequence:
            sequence = [start_stage]

        end_date = parse_trade_date(args.end_date)
        api_key, api_secret = cfg.require_alpaca_credentials()
        broker = AlpacaBroker(api_key, api_secret, paper=True)

        prepared_cache: dict[str, dict[str, Any]] = {}

        def stage_inputs(stage: str) -> dict[str, Any]:
            if stage in prepared_cache:
                return prepared_cache[stage]
            state["current_stage"] = _stage_running_name(stage)
            _log(f"stage={stage} action=prepare_inputs feed={str(args.feed).strip().lower() or DEFAULT_FEED}")
            _set_activity(state, ctx, activity=f"{stage}:preparing_inputs")
            if stage in {"registry_screen", "stability_expand"}:
                prepared_cache[stage] = _prepare_stage_inputs(
                    cfg=cfg,
                    broker=broker,
                    end_date=end_date,
                    feed=str(args.feed).strip().lower() or DEFAULT_FEED,
                    train_days=SCREEN_TRAIN_DAYS,
                    oos_days=SCREEN_OOS_DAYS,
                    test_days=SCREEN_TEST_DAYS,
                )
            else:
                prepared_cache[stage] = _prepare_stage_inputs(
                    cfg=cfg,
                    broker=broker,
                    end_date=end_date,
                    feed=str(args.feed).strip().lower() or DEFAULT_FEED,
                    train_days=VALIDATION_TRAIN_DAYS,
                    oos_days=VALIDATION_OOS_DAYS,
                    test_days=VALIDATION_TEST_DAYS,
                )
            _log(f"stage={stage} action=prepared_inputs")
            _set_activity(state, ctx, activity=f"{stage}:inputs_ready")
            return prepared_cache[stage]

        started = False
        for stage in sequence:
            if not started:
                if stage != start_stage:
                    continue
                started = True
            if time.monotonic() >= ctx.deadline_monotonic:
                state["current_stage"] = _stage_pending_name(stage)
                _set_state_checkpoint(state, ctx)
                _write_shadow_artifacts(ctx, state=state)
                _write_search_report(ctx, state=state)
                return 0
            completed = _process_stage(
                ctx,
                stage=stage,
                prepared=stage_inputs(stage),
                state=state,
            )
            _write_shadow_artifacts(ctx, state=state)
            _write_search_report(ctx, state=state)
            if not completed:
                return 0

        state["current_stage"] = "shadow_finalized"
        _write_shadow_artifacts(ctx, state=state)
        _clear_activity(state, ctx)
        _write_search_report(ctx, state=state)
        _log(f"search_run_dir: {ctx.run_dir}")
        _log(f"shadow_strategy_file: {ctx.global_shadow_path}")
        _log(f"current_stage: {state['current_stage']}")
        return 0
    finally:
        _release_global_lock(ctx.global_lock_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the resumable nightly Alpaca alpha search pipeline."
    )
    parser.add_argument("--run-id", default="", help="Existing search run ID to resume or inspect.")
    parser.add_argument("--new-run", action="store_true", help="Start a new search run.")
    parser.add_argument("--resume", action="store_true", help="Resume the latest or provided run ID.")
    parser.add_argument("--screen-only", action="store_true", help="Run only the fast registry screening stage.")
    parser.add_argument("--mutation-only", action="store_true", help="Run only the mutation stage.")
    parser.add_argument("--status", action="store_true", help="Print status for the latest or provided run.")
    parser.add_argument(
        "--stage",
        choices=["auto", "registry_screen", "stability_expand", "full_validation", "mutation"],
        default="auto",
        help="Optional stage override.",
    )
    parser.add_argument("--end-date", default="", help="Latest completed date in YYYY-MM-DD.")
    parser.add_argument("--feed", default=DEFAULT_FEED, help="Historical feed for search evaluation.")
    parser.add_argument("--promotion-profile", default=DEFAULT_PROMOTION_PROFILE)
    parser.add_argument("--batch-size", type=int, default=0, help="Max candidates processed per invocation.")
    parser.add_argument("--max-runtime-min", type=int, default=0, help="Max wall-clock runtime for this invocation.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(run_search(args))


if __name__ == "__main__":
    raise SystemExit(main())
