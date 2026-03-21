from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any


class ResearchBaselineError(ValueError):
    pass


def _parse_date(value: Any, *, field_name: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise ResearchBaselineError(f"Research baseline missing required date field: {field_name}")
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ResearchBaselineError(
            f"Research baseline field {field_name} must be YYYY-MM-DD, got {value!r}"
        ) from exc


def _parse_truncation(values: list[Any]) -> list[float | None]:
    out: list[float | None] = []
    for value in values:
        if value in {"", None, "none", "null", "None", "NULL"}:
            out.append(None)
        else:
            out.append(float(value))
    return out


@dataclass(frozen=True, slots=True)
class ResearchBaseline:
    baseline_id: str
    description: str
    feed: str
    end_date: date
    classification_snapshot_date: date
    train_days: int
    oos_days: int
    test_days: int
    group_level_grid: list[str]
    book_mode_grid: list[str]
    top_n_grid: list[int]
    decay_grid: list[int]
    truncation_grid: list[float | None]
    gross_exposure: float
    alpha_set: str
    min_universe: int = 2500
    min_universe_ratio: float = 0.90
    notes: list[str] | None = None

    @property
    def latest_completed_date(self) -> date:
        return self.end_date


def load_research_baseline(path: Path) -> ResearchBaseline:
    if not path.exists():
        raise ResearchBaselineError(f"Research baseline file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ResearchBaselineError("Research baseline must be a JSON object.")

    baseline_id = str(payload.get("baseline_id", "")).strip()
    if not baseline_id:
        raise ResearchBaselineError("Research baseline missing baseline_id.")

    group_level_grid = [str(value).strip().lower() for value in payload.get("group_level_grid", []) if str(value).strip()]
    book_mode_grid = [str(value).strip().lower() for value in payload.get("book_mode_grid", []) if str(value).strip()]
    top_n_grid = [int(value) for value in payload.get("top_n_grid", [])]
    decay_grid = [int(value) for value in payload.get("decay_grid", [])]
    truncation_grid = _parse_truncation(list(payload.get("truncation_grid", [])))

    if not group_level_grid:
        raise ResearchBaselineError("Research baseline must declare group_level_grid.")
    if not book_mode_grid:
        raise ResearchBaselineError("Research baseline must declare book_mode_grid.")
    if not top_n_grid:
        raise ResearchBaselineError("Research baseline must declare top_n_grid.")
    if not decay_grid:
        raise ResearchBaselineError("Research baseline must declare decay_grid.")
    if not truncation_grid:
        raise ResearchBaselineError("Research baseline must declare truncation_grid.")

    notes_raw = payload.get("notes", [])
    notes = [str(note).strip() for note in notes_raw if str(note).strip()]

    return ResearchBaseline(
        baseline_id=baseline_id,
        description=str(payload.get("description", "")).strip(),
        feed=str(payload.get("feed", "sip")).strip().lower() or "sip",
        end_date=_parse_date(payload.get("end_date"), field_name="end_date"),
        classification_snapshot_date=_parse_date(
            payload.get("classification_snapshot_date"),
            field_name="classification_snapshot_date",
        ),
        train_days=int(payload.get("train_days", 756)),
        oos_days=int(payload.get("oos_days", 252)),
        test_days=int(payload.get("test_days", 252)),
        group_level_grid=group_level_grid,
        book_mode_grid=book_mode_grid,
        top_n_grid=top_n_grid,
        decay_grid=decay_grid,
        truncation_grid=truncation_grid,
        gross_exposure=float(payload.get("gross_exposure", 4.0)),
        alpha_set=str(payload.get("alpha_set", "literature_core")).strip() or "literature_core",
        min_universe=int(payload.get("min_universe", 2500)),
        min_universe_ratio=float(payload.get("min_universe_ratio", 0.90)),
        notes=notes,
    )
