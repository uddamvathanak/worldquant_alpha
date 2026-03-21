from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from alpha_registry import AlphaDefinition, get_alpha_definition


ALLOWED_GROUP_LEVELS = {"market", "sector", "industry"}
ALLOWED_BOOK_MODES = {"sector", "none", "sector_weighted", "none_weighted"}
ALLOWED_TOP_N = {30, 50, 75, 100, 200, 500, 1000, 3000}
ALLOWED_SIGNAL_DECAY = {0, 3, 5, 10}
ALLOWED_SCORE_TRUNCATION = {None, 0.05, 0.10}


class AlphaDslError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AlphaDslCandidate:
    template_name: str
    family: str
    params: dict[str, Any]
    group_level: str
    book_mode: str
    top_n: int
    signal_decay: int
    score_truncation: float | None
    source: str
    parent_candidates: list[str]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_name": self.template_name,
            "family": self.family,
            "params": dict(self.params),
            "group_level": self.group_level,
            "book_mode": self.book_mode,
            "top_n": self.top_n,
            "signal_decay": self.signal_decay,
            "score_truncation": self.score_truncation,
            "source": self.source,
            "parent_candidates": list(self.parent_candidates),
            "notes": self.notes,
        }


def _normalize_float(value: Any) -> float:
    if isinstance(value, bool):
        raise AlphaDslError(f"Boolean value is not a valid numeric alpha parameter: {value}")
    return float(value)


def _normalize_param_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            return text
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    return value


def _normalize_score_truncation(value: Any) -> float | None:
    if value in {"", None, "none", "null", "None", "NULL"}:
        return None
    truncation = round(_normalize_float(value), 2)
    if truncation not in {0.05, 0.10}:
        raise AlphaDslError(f"Unsupported score_truncation: {value}")
    return truncation


def _normalize_params(definition: AlphaDefinition, params: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = set(definition.default_params) | set(definition.parameter_grid)
    unknown = sorted(set(params) - allowed_keys)
    if unknown:
        raise AlphaDslError(
            f"Template {definition.name} received unsupported param keys: {', '.join(unknown)}"
        )

    normalized: dict[str, Any] = dict(definition.default_params)
    for key, value in params.items():
        normalized[key] = _normalize_param_value(value)

    for key, allowed_values in definition.parameter_grid.items():
        candidate_value = normalized.get(key)
        normalized_allowed = [_normalize_param_value(item) for item in allowed_values]
        if candidate_value not in normalized_allowed:
            raise AlphaDslError(
                f"Template {definition.name} param {key}={candidate_value!r} is out of bounds. "
                f"Allowed values: {normalized_allowed}"
            )

    for key, default_value in definition.default_params.items():
        if key in definition.parameter_grid:
            continue
        normalized[key] = _normalize_param_value(normalized.get(key, default_value))

    return normalized


def normalize_candidate_payload(payload: dict[str, Any]) -> AlphaDslCandidate:
    template_name = str(payload.get("template_name", "")).strip().lower()
    if not template_name:
        raise AlphaDslError("Mutation candidate is missing template_name.")
    definition = get_alpha_definition(template_name)

    family = str(payload.get("family", definition.family)).strip().lower() or definition.family
    if family != definition.family:
        raise AlphaDslError(
            f"Template {template_name} must stay within family {definition.family}, got {family}."
        )

    params_raw = payload.get("params", {})
    if not isinstance(params_raw, dict):
        raise AlphaDslError("Mutation candidate params must be a JSON object.")
    params = _normalize_params(definition, params_raw)

    group_level = str(payload.get("group_level", "sector")).strip().lower() or "sector"
    if group_level not in ALLOWED_GROUP_LEVELS:
        raise AlphaDslError(f"Unsupported group_level: {group_level}")

    book_mode = str(payload.get("book_mode", "sector")).strip().lower() or "sector"
    if book_mode not in ALLOWED_BOOK_MODES:
        raise AlphaDslError(f"Unsupported book_mode: {book_mode}")

    top_n = int(payload.get("top_n", 50))
    if top_n not in ALLOWED_TOP_N:
        raise AlphaDslError(f"Unsupported top_n: {top_n}")

    signal_decay = int(payload.get("signal_decay", 0))
    if signal_decay not in ALLOWED_SIGNAL_DECAY:
        raise AlphaDslError(f"Unsupported signal_decay: {signal_decay}")

    score_truncation = _normalize_score_truncation(payload.get("score_truncation", None))
    if score_truncation not in ALLOWED_SCORE_TRUNCATION:
        raise AlphaDslError(f"Unsupported score_truncation: {score_truncation}")

    source = str(payload.get("source", "")).strip() or "mutation"
    parent_candidates_raw = payload.get("parent_candidates", [])
    if not isinstance(parent_candidates_raw, list):
        raise AlphaDslError("Mutation candidate parent_candidates must be a JSON array.")
    parent_candidates = [
        str(item).strip()
        for item in parent_candidates_raw
        if str(item).strip()
    ]
    notes = str(payload.get("notes", "")).strip()

    return AlphaDslCandidate(
        template_name=template_name,
        family=family,
        params=params,
        group_level=group_level,
        book_mode=book_mode,
        top_n=top_n,
        signal_decay=signal_decay,
        score_truncation=score_truncation,
        source=source,
        parent_candidates=parent_candidates,
        notes=notes,
    )


def candidate_signature(candidate: AlphaDslCandidate | dict[str, Any]) -> str:
    normalized = candidate if isinstance(candidate, AlphaDslCandidate) else normalize_candidate_payload(candidate)
    trunc = "none" if normalized.score_truncation is None else f"{normalized.score_truncation:.2f}"
    params_key = json.dumps(normalized.params, sort_keys=True, separators=(",", ":"))
    return (
        f"{normalized.template_name}|{normalized.family}|{params_key}|{normalized.group_level}|"
        f"{normalized.book_mode}|{normalized.top_n}|{normalized.signal_decay}|{trunc}"
    )


def validate_candidate_batch(payload: Any, *, max_candidates: int = 20) -> list[AlphaDslCandidate]:
    if not isinstance(payload, list):
        raise AlphaDslError("Mutation response must be a JSON array of candidates.")
    if len(payload) > int(max_candidates):
        raise AlphaDslError(
            f"Mutation response returned {len(payload)} candidates, above max {int(max_candidates)}."
        )

    out: list[AlphaDslCandidate] = []
    seen: set[str] = set()
    for raw_candidate in payload:
        if not isinstance(raw_candidate, dict):
            raise AlphaDslError("Each mutation candidate must be a JSON object.")
        candidate = normalize_candidate_payload(raw_candidate)
        signature = candidate_signature(candidate)
        if signature in seen:
            continue
        seen.add(signature)
        out.append(candidate)
    return out
