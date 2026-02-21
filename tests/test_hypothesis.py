from pathlib import Path

from worldquant_alpha.hypothesis import HypothesisStore


def test_create_and_update_hypothesis_annotations(tmp_path: Path) -> None:
    path = tmp_path / "hypotheses.jsonl"
    store = HypothesisStore(path)

    created = store.create(
        title="Test",
        rationale="Base rationale",
        expression="rank(vwap/close)",
        fields_used=["vwap", "close"],
        economic_hypothesis="Initial economic story",
    )

    updated = store.update(
        created.hypothesis_id,
        behavioral_mechanism="Flow pressure mean reverts",
        risk_hypothesis="Momentum regime risk",
        failure_modes="Trend shock days",
    )
    assert updated is not None
    assert updated.economic_hypothesis == "Initial economic story"
    assert updated.behavioral_mechanism == "Flow pressure mean reverts"
    assert updated.risk_hypothesis == "Momentum regime risk"
    assert updated.failure_modes == "Trend shock days"
