from pathlib import Path

from worldquant_alpha.logging_store import ExperimentStore, RunRecord


def test_log_and_read_run(tmp_path: Path) -> None:
    db_path = tmp_path / "experiments.db"
    store = ExperimentStore(db_path)

    record = RunRecord(
        run_id="run123",
        run_at="2026-02-11T00:00:00+00:00",
        hypothesis_id="hyp123",
        title="Test Hypothesis",
        expression="rank(vwap/close)",
        dataset="sim-001",
        notes="note",
        metrics={"fitness": 1.2, "margin": 30.0},
        settings={"settings_profile": "baseline_d1"},
        status="keep",
        why_worked="Signal aligned with mean reversion",
        why_failed="",
        economic_intuition="Close dislocation reverts",
        next_step="Test in alternate universe",
    )
    store.log_run(record)

    runs = store.list_runs(limit=5)
    assert not runs.empty
    assert "metric_fitness" in runs.columns
    assert runs.iloc[0]["status"] == "keep"

    fetched = store.get_run("run123")
    assert fetched is not None
    assert fetched["metrics"]["fitness"] == 1.2
    assert fetched["economic_intuition"] == "Close dislocation reverts"
