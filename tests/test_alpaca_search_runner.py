from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
import sys

import pandas as pd
import pytest


ALPACA_DIR = Path(__file__).resolve().parents[1] / "paper" / "alpaca"
if str(ALPACA_DIR) not in sys.path:
    sys.path.insert(0, str(ALPACA_DIR))

import search_runner  # type: ignore  # noqa: E402
from backtest_engine import ResearchCandidate  # type: ignore  # noqa: E402


def _cfg(tmp_path: Path) -> SimpleNamespace:
    private_dir = tmp_path / "private"
    state_dir = tmp_path / "state"
    baseline_file = tmp_path / "research_baseline.json"
    baseline_file.write_text(
        """
{
  "baseline_id": "test_baseline",
  "description": "test",
  "feed": "sip",
  "end_date": "2026-03-19",
  "classification_snapshot_date": "2026-03-17",
  "train_days": 756,
  "oos_days": 252,
  "test_days": 252,
  "group_level_grid": ["sector", "industry"],
  "book_mode_grid": ["sector_weighted"],
  "top_n_grid": [3000],
  "decay_grid": [0, 3, 5],
  "truncation_grid": [null, 0.05],
  "gross_exposure": 4.0,
  "alpha_set": "literature_core",
  "min_universe": 2500,
  "min_universe_ratio": 0.9
}
        """.strip(),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        private_dir=private_dir,
        state_dir=state_dir,
        search_runs_dir=private_dir / "search_runs",
        shadow_strategy_file=private_dir / "shadow_strategy.json",
        selected_strategy_file=private_dir / "selected_strategy.json",
        research_baseline_file=baseline_file,
        research_cache_db_path=state_dir / "research_cache.db",
        gross_exposure=4.0,
        round_trip_cost_bps=5.0,
        alpha_search_batch_size=10,
        alpha_search_max_runtime_min=480,
        require_alpaca_credentials=lambda: ("key", "secret"),
    )
    cfg.search_runs_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def _args(**overrides: object) -> Namespace:
    base = dict(
        run_id="",
        new_run=True,
        resume=False,
        screen_only=False,
        mutation_only=False,
        status=False,
        stage="auto",
        end_date="2026-03-19",
        feed="sip",
        baseline_file="",
        dynamic_baseline=False,
        promotion_profile="balanced",
        batch_size=0,
        max_runtime_min=0,
    )
    base.update(overrides)
    return Namespace(**base)


def _context(tmp_path: Path, *, run_id: str = "20260320T000000Z") -> search_runner.SearchContext:
    cfg = _cfg(tmp_path)
    return search_runner._search_context(cfg, run_id=run_id, batch_size=10, max_runtime_min=10)


def _baseline(tmp_path: Path):  # type: ignore[no-untyped-def]
    cfg = _cfg(tmp_path)
    return search_runner._load_baseline(cfg, _args())


def test_search_runner_parser_accepts_status_and_mutation_flags() -> None:
    parser = search_runner.build_parser()
    args = parser.parse_args(["--status", "--run-id", "abc", "--mutation-only", "--dynamic-baseline"])
    assert args.status is True
    assert args.run_id == "abc"
    assert args.mutation_only is True
    assert args.dynamic_baseline is True


def test_determine_start_stage_advances_after_completed_stage() -> None:
    state = {
        "current_stage": "registry_screen_complete",
        "completed_stages": ["registry_screen_complete"],
    }

    stage = search_runner._determine_start_stage(state, args=_args())

    assert stage == "stability_expand"


def test_stage_b_candidates_only_expand_phase_a_survivors(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    search_runner._append_result_row(
        search_runner._stage_results_path(ctx, "registry_screen"),
        {
            "candidate_id": "registry_screen:good",
            "candidate_name": "good",
            "alpha_name": "smooth_momentum",
            "family": "momentum",
            "params": {"window": 20},
            "group_level": "sector",
            "book_mode": "sector",
            "top_n": 50,
            "gross_exposure": 4.0,
            "signal_decay": 0,
            "score_truncation": None,
            "oos_returns": 0.1,
            "oos_fitness_proxy": 0.2,
            "oos_sharpe_proxy": 0.6,
            "oos_max_drawdown": 0.1,
            "oos_days_with_full_book_ratio": 0.95,
            "test_returns": 0.2,
            "test_fitness_proxy": 0.3,
            "test_sharpe_proxy": 0.8,
            "test_max_drawdown": 0.1,
            "test_turnover_mean": 2.0,
        },
    )
    search_runner._append_result_row(
        search_runner._stage_results_path(ctx, "registry_screen"),
        {
            "candidate_id": "registry_screen:bad",
            "candidate_name": "bad",
            "alpha_name": "rev_close_1d",
            "family": "short_reversion",
            "params": {},
            "group_level": "sector",
            "book_mode": "sector",
            "top_n": 50,
            "gross_exposure": 4.0,
            "signal_decay": 0,
            "score_truncation": None,
            "oos_returns": -0.1,
            "oos_fitness_proxy": -0.2,
            "oos_sharpe_proxy": -0.6,
            "oos_max_drawdown": 0.3,
            "oos_days_with_full_book_ratio": 0.80,
            "test_returns": -0.2,
            "test_fitness_proxy": -0.3,
            "test_sharpe_proxy": -0.8,
            "test_max_drawdown": 0.3,
            "test_turnover_mean": 4.0,
        },
    )

    out = search_runner._stage_b_candidates(ctx, gross_exposure=4.0)

    assert len(out) == 12
    assert all(record["alpha_name"] == "smooth_momentum" for record in out)
    assert all(record["book_mode"] == "sector_weighted" for record in out)
    assert all(int(record["top_n"]) == 3000 for record in out)


def test_stage_c_candidates_tolerate_missing_positive_month_ratio(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    search_runner._append_result_row(
        search_runner._stage_results_path(ctx, "stability_expand"),
        {
            "candidate_id": "stability_expand:good",
            "candidate_name": "good",
            "alpha_name": "smooth_momentum",
            "family": "momentum",
            "params": {"window": 20},
            "group_level": "sector",
            "book_mode": "sector_weighted",
            "top_n": 3000,
            "gross_exposure": 4.0,
            "signal_decay": 0,
            "score_truncation": None,
            "oos_returns": 0.1,
            "oos_fitness_proxy": 0.2,
            "oos_sharpe_proxy": 0.6,
            "oos_max_drawdown": 0.1,
            "oos_days_with_full_book_ratio": 0.95,
            "test_returns": 0.2,
            "test_fitness_proxy": 0.3,
            "test_sharpe_proxy": 0.8,
            "test_max_drawdown": 0.1,
            "test_turnover_mean": 2.0,
        },
    )

    out = search_runner._stage_c_candidates(ctx, gross_exposure=4.0)

    assert out == []


def test_process_stage_skips_completed_candidates_on_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _context(tmp_path)
    candidate_a = ResearchCandidate(
        alpha_name="smooth_momentum",
        family="momentum",
        params={"window": 20},
        group_level="sector",
        book_mode="sector",
        top_n=50,
        gross_exposure=4.0,
    )
    candidate_b = ResearchCandidate(
        alpha_name="breakout_quality",
        family="momentum",
        params={"window": 20},
        group_level="sector",
        book_mode="sector",
        top_n=50,
        gross_exposure=4.0,
    )
    queue_records = [
        search_runner._queue_record_from_candidate("registry_screen", candidate_a, source="registry_screen"),
        search_runner._queue_record_from_candidate("registry_screen", candidate_b, source="registry_screen"),
    ]
    search_runner._write_jsonl(ctx.queue_path, queue_records)
    search_runner._append_result_row(
        search_runner._stage_results_path(ctx, "registry_screen"),
        {
            "candidate_id": queue_records[0]["candidate_id"],
            "candidate_name": queue_records[0]["candidate_name"],
            "alpha_name": "smooth_momentum",
            "family": "momentum",
            "params": {"window": 20},
            "group_level": "sector",
            "book_mode": "sector",
            "top_n": 50,
            "gross_exposure": 4.0,
            "signal_decay": 0,
            "score_truncation": None,
            "test_returns": 0.1,
            "test_fitness_proxy": 0.2,
            "test_sharpe_proxy": 0.3,
            "test_max_drawdown": 0.1,
            "test_turnover_mean": 2.0,
        },
    )

    called: list[str] = []

    def fake_eval(*, prepared, round_trip_cost_bps, record):  # type: ignore[no-untyped-def]
        called.append(record["candidate_name"])
        return {
            "candidate_id": record["candidate_id"],
            "candidate_name": record["candidate_name"],
            "alpha_name": record["alpha_name"],
            "family": record["family"],
            "params": record["params"],
            "group_level": record["group_level"],
            "book_mode": record["book_mode"],
            "top_n": record["top_n"],
            "gross_exposure": record["gross_exposure"],
            "signal_decay": record["signal_decay"],
            "score_truncation": record["score_truncation"],
            "test_returns": 0.1,
            "test_fitness_proxy": 0.2,
            "test_sharpe_proxy": 0.3,
            "test_max_drawdown": 0.1,
            "test_turnover_mean": 2.0,
        }

    monkeypatch.setattr(search_runner, "_evaluate_candidate_record", fake_eval)
    state = search_runner._base_state(ctx.run_id, args=_args(), cfg=ctx.cfg, baseline=_baseline(tmp_path))
    prepared = {
        "splits": SimpleNamespace(
            latest_completed_date=pd.Timestamp("2026-03-19").date(),
            train_dates=[pd.Timestamp("2025-01-01").date()] * 252,
            oos_dates=[pd.Timestamp("2026-01-01").date()] * 63,
            test_dates=[pd.Timestamp("2026-03-01").date()] * 63,
        ),
        "classification_snapshot_path": Path("C:/tmp/classifications_latest.csv"),
    }

    completed = search_runner._process_stage(
        ctx,
        stage="registry_screen",
        prepared=prepared,
        state=state,
    )

    assert completed is True
    assert called == [queue_records[1]["candidate_name"]]


def test_process_stage_uses_cross_run_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _context(tmp_path)
    candidate = ResearchCandidate(
        alpha_name="smooth_momentum",
        family="momentum",
        params={"window": 20},
        group_level="sector",
        book_mode="sector",
        top_n=50,
        gross_exposure=4.0,
    )
    record = search_runner._queue_record_from_candidate("registry_screen", candidate, source="registry_screen")
    search_runner._write_jsonl(ctx.queue_path, [record])
    prepared = {
        "splits": SimpleNamespace(
            latest_completed_date=pd.Timestamp("2026-03-19").date(),
            train_dates=[pd.Timestamp("2025-01-01").date()] * 252,
            oos_dates=[pd.Timestamp("2026-01-01").date()] * 63,
            test_dates=[pd.Timestamp("2026-03-01").date()] * 63,
        ),
        "classification_snapshot_path": Path("C:/tmp/classifications_latest.csv"),
    }
    evaluation_key = search_runner.build_evaluation_key(
        feed="sip",
        latest_completed_date="2026-03-19",
        train_days=252,
        oos_days=63,
        test_days=63,
        classification_snapshot=Path("C:/tmp/classifications_latest.csv"),
        round_trip_cost_bps=5.0,
    )
    signature = search_runner._candidate_cache_signature(record)
    ctx.research_cache.put(
        evaluation_key=evaluation_key,
        candidate_signature=signature,
        candidate_name=record["candidate_name"],
        result={
            "candidate_id": record["candidate_id"],
            "candidate_name": record["candidate_name"],
            "alpha_name": record["alpha_name"],
            "family": record["family"],
            "params": record["params"],
            "group_level": record["group_level"],
            "book_mode": record["book_mode"],
            "top_n": record["top_n"],
            "gross_exposure": record["gross_exposure"],
            "signal_decay": record["signal_decay"],
            "score_truncation": record["score_truncation"],
            "test_returns": 0.1,
            "test_fitness_proxy": 0.2,
            "test_sharpe_proxy": 0.3,
            "test_max_drawdown": 0.1,
            "test_turnover_mean": 2.0,
        },
    )

    def fail_eval(*, prepared, round_trip_cost_bps, record):  # type: ignore[no-untyped-def]
        raise AssertionError("cache should have prevented live evaluation")

    monkeypatch.setattr(search_runner, "_evaluate_candidate_record", fail_eval)
    state = search_runner._base_state(ctx.run_id, args=_args(), cfg=ctx.cfg, baseline=_baseline(tmp_path))

    completed = search_runner._process_stage(
        ctx,
        stage="registry_screen",
        prepared=prepared,
        state=state,
    )

    assert completed is True
    out = search_runner._deserialize_results(search_runner._stage_results_path(ctx, "registry_screen"))
    assert int(out.iloc[0]["cache_hit"]) == 1


def test_mutation_stage_marks_generator_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _context(tmp_path)
    state = search_runner._base_state(ctx.run_id, args=_args(mutation_only=True), cfg=ctx.cfg, baseline=_baseline(tmp_path))
    monkeypatch.setattr(search_runner, "generator_enabled", lambda: False)

    records = search_runner._stage_queue_records(ctx, "mutation", state=state)

    assert records == []
    assert "generator_disabled" in state["notes"]


def test_write_shadow_artifacts_preserves_selected_strategy_file(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    search_runner._append_result_row(
        search_runner._stage_results_path(ctx, "full_validation"),
        {
            "candidate_id": "full_validation:candidate",
            "candidate_name": "candidate",
            "alpha_name": "smooth_momentum",
            "family": "momentum",
            "params": {"window": 20},
            "group_level": "sector",
            "book_mode": "sector",
            "top_n": 50,
            "gross_exposure": 4.0,
            "signal_decay": 3,
            "score_truncation": 0.05,
            "oos_returns": -0.1,
            "oos_fitness_proxy": -0.1,
            "oos_sharpe_proxy": -0.1,
            "oos_max_drawdown": 0.2,
            "oos_days_with_full_book_ratio": 0.95,
            "test_returns": -0.2,
            "test_fitness_proxy": -0.3,
            "test_sharpe_proxy": -0.4,
            "test_max_drawdown": 0.25,
            "test_turnover_mean": 2.0,
            "test_positive_month_ratio": 0.40,
            "test_days_with_full_book_ratio": 0.95,
        },
    )
    state = search_runner._base_state(ctx.run_id, args=_args(), cfg=ctx.cfg, baseline=_baseline(tmp_path))

    search_runner._write_shadow_artifacts(ctx, state=state)

    assert ctx.shadow_strategy_path.exists()
    assert ctx.global_shadow_path.exists()
    assert ctx.cfg.selected_strategy_file.exists() is False


def test_write_search_report_mentions_shadow_candidate(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    state = search_runner._base_state(ctx.run_id, args=_args(), cfg=ctx.cfg, baseline=_baseline(tmp_path))
    search_runner._write_json(
        ctx.summary_path,
        {
            "candidate_name": "candidate_a",
            "shadow_stage": "full_validation",
            "unseen_bar_passed": False,
            "reason": "No candidate has passed the unseen promotion bar yet; best shadow candidate retained.",
        },
    )

    search_runner._write_search_report(ctx, state=state)

    text = ctx.report_path.read_text(encoding="utf-8")
    assert "candidate_a" in text
    assert "unseen_bar_passed: 0" in text


def test_run_search_exits_already_running_when_lock_is_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = _cfg(tmp_path)
    lock_path = cfg.private_dir / search_runner.GLOBAL_LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    import os

    lock_path.write_text('{"pid": %d, "run_id": "busy"}' % os.getpid(), encoding="utf-8")

    monkeypatch.setattr(search_runner, "load_config", lambda: cfg)

    exit_code = search_runner.run_search(_args())

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "already_running" in captured.out
