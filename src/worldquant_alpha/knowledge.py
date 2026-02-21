from __future__ import annotations

from pathlib import Path
import json
import re

import pandas as pd


DEFAULT_FIELD_CATALOG = Path("knowledge/field_encyclopedia.csv")
DEFAULT_TEMPLATE_MAP = Path("knowledge/alpha_template_map.csv")
DEFAULT_SETTINGS_PROFILES = Path("knowledge/settings_profiles.csv")
FIELD_CATALOG_COLUMNS = [
    "field",
    "category",
    "description",
    "alpha_use_cases",
    "data_quality_checks",
    "notes",
]
FIELD_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_\.]*$")
KEY_ALIASES = {
    "field": "field",
    "name": "field",
    "field name": "field",
    "category": "category",
    "type": "category",
    "description": "description",
    "desc": "description",
    "alpha use cases": "alpha_use_cases",
    "alpha use case": "alpha_use_cases",
    "use cases": "alpha_use_cases",
    "use case": "alpha_use_cases",
    "data quality checks": "data_quality_checks",
    "data quality check": "data_quality_checks",
    "quality checks": "data_quality_checks",
    "quality check": "data_quality_checks",
    "notes": "notes",
}


def _parse_pipe_list(value: str) -> list[str]:
    if not isinstance(value, str):
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def load_field_catalog(path: Path = DEFAULT_FIELD_CATALOG) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=FIELD_CATALOG_COLUMNS)
    frame = pd.read_csv(path)
    for col in FIELD_CATALOG_COLUMNS:
        if col not in frame.columns:
            frame[col] = ""
    frame = frame[FIELD_CATALOG_COLUMNS].copy()
    frame["field"] = frame["field"].astype(str).str.strip().str.lower()
    frame["category"] = frame["category"].astype(str).str.strip()
    return frame


def query_field_catalog(
    frame: pd.DataFrame,
    *,
    query: str = "",
    category: str = "",
) -> pd.DataFrame:
    out = frame.copy()
    if category:
        out = out[out["category"].str.lower() == category.strip().lower()]
    if query:
        q = query.strip().lower()
        out = out[
            out["field"].str.lower().str.contains(q)
            | out["description"].str.lower().str.contains(q)
            | out["alpha_use_cases"].str.lower().str.contains(q)
        ]
    return out.sort_values(["category", "field"]).reset_index(drop=True)


def _normalize_field_value(value: str) -> str:
    return value.strip().lower()


def _is_valid_field_name(name: str) -> bool:
    return bool(FIELD_NAME_PATTERN.match(name.strip()))


def _build_entry(
    *,
    field: str,
    description: str,
    category: str,
    alpha_use_cases: str = "",
    data_quality_checks: str = "",
    notes: str = "",
) -> dict[str, str] | None:
    field_key = _normalize_field_value(field)
    if not field_key or not _is_valid_field_name(field_key):
        return None
    if not description.strip():
        return None
    return {
        "field": field_key,
        "category": category.strip(),
        "description": description.strip(),
        "alpha_use_cases": alpha_use_cases.strip(),
        "data_quality_checks": data_quality_checks.strip(),
        "notes": notes.strip(),
    }


def _dedupe_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for entry in entries:
        out[entry["field"]] = entry
    return list(out.values())


def _extract_key_value_blocks(
    lines: list[str],
    default_category: str,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    seen_any = False

    def flush() -> None:
        nonlocal current
        if "field" in current and "description" in current:
            entry = _build_entry(
                field=current.get("field", ""),
                description=current.get("description", ""),
                category=current.get("category", default_category),
                alpha_use_cases=current.get("alpha_use_cases", ""),
                data_quality_checks=current.get("data_quality_checks", ""),
                notes=current.get("notes", ""),
            )
            if entry is not None:
                entries.append(entry)
        current = {}

    for line in lines:
        text = line.strip()
        if not text:
            flush()
            continue
        if ":" not in text:
            continue
        key_raw, value_raw = text.split(":", 1)
        key = key_raw.strip().lower()
        key = KEY_ALIASES.get(key, "")
        if not key:
            continue
        seen_any = True
        current[key] = value_raw.strip()
    flush()
    return entries if seen_any else []


def _extract_line_entries(
    lines: list[str],
    default_category: str,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []

    for line in lines:
        text = line.strip()
        if not text:
            continue
        text = re.sub(r"^[-*]\s+", "", text)
        lower = text.lower()
        if "field" in lower and "description" in lower:
            continue

        tab_parts = [part.strip() for part in text.split("\t") if part.strip()]
        if len(tab_parts) >= 2 and _is_valid_field_name(tab_parts[0]):
            category = tab_parts[2] if len(tab_parts) >= 3 else default_category
            entry = _build_entry(
                field=tab_parts[0],
                description=tab_parts[1],
                category=category,
            )
            if entry is not None:
                entries.append(entry)
                continue

        m = re.match(
            r"^([A-Za-z_][A-Za-z0-9_\.]*)\s*\(([^)]+)\)\s*[:\-]\s*(.+)$",
            text,
        )
        if m:
            entry = _build_entry(
                field=m.group(1),
                category=m.group(2),
                description=m.group(3),
            )
            if entry is not None:
                entries.append(entry)
                continue

        m = re.match(r"^([A-Za-z_][A-Za-z0-9_\.]*)\s*[:\-]\s*(.+)$", text)
        if m:
            entry = _build_entry(
                field=m.group(1),
                category=default_category,
                description=m.group(2),
            )
            if entry is not None:
                entries.append(entry)
                continue

        parts = re.split(r"\s{2,}", text)
        if len(parts) >= 2 and _is_valid_field_name(parts[0]):
            category = parts[2] if len(parts) >= 3 else default_category
            entry = _build_entry(
                field=parts[0],
                category=category,
                description=parts[1],
            )
            if entry is not None:
                entries.append(entry)
                continue

    return entries


def parse_field_entries_text(
    raw_text: str,
    *,
    default_category: str = "Unknown",
) -> list[dict[str, str]]:
    text = (raw_text or "").strip()
    if not text:
        return []

    parsed_json: object | None = None
    if text.startswith("{") or text.startswith("["):
        try:
            parsed_json = json.loads(text)
        except json.JSONDecodeError:
            parsed_json = None

    if isinstance(parsed_json, dict):
        parsed_json = [parsed_json]
    if isinstance(parsed_json, list):
        entries: list[dict[str, str]] = []
        for item in parsed_json:
            if not isinstance(item, dict):
                continue
            entry = _build_entry(
                field=str(item.get("field", item.get("name", ""))),
                description=str(item.get("description", item.get("desc", ""))),
                category=str(item.get("category", default_category)),
                alpha_use_cases=str(
                    item.get("alpha_use_cases", item.get("use_cases", ""))
                ),
                data_quality_checks=str(
                    item.get(
                        "data_quality_checks",
                        item.get("quality_checks", ""),
                    )
                ),
                notes=str(item.get("notes", "")),
            )
            if entry is not None:
                entries.append(entry)
        if entries:
            return _dedupe_entries(entries)

    lines = text.splitlines()
    block_entries = _extract_key_value_blocks(lines, default_category)
    if block_entries:
        return _dedupe_entries(block_entries)
    return _dedupe_entries(_extract_line_entries(lines, default_category))


def upsert_field_catalog_entry(
    path: Path,
    *,
    field: str,
    category: str,
    description: str,
    alpha_use_cases: str = "",
    data_quality_checks: str = "",
    notes: str = "",
) -> pd.DataFrame:
    field_key = _normalize_field_value(field)
    if not field_key:
        raise ValueError("field is required")

    frame = load_field_catalog(path)
    payload = {
        "field": field_key,
        "category": category.strip(),
        "description": description.strip(),
        "alpha_use_cases": alpha_use_cases.strip(),
        "data_quality_checks": data_quality_checks.strip(),
        "notes": notes.strip(),
    }

    if field_key in set(frame["field"]):
        for col, value in payload.items():
            frame.loc[frame["field"] == field_key, col] = value
    else:
        frame = pd.concat([frame, pd.DataFrame([payload])], ignore_index=True)

    frame = frame.sort_values(["category", "field"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame


def upsert_field_catalog_entries(
    path: Path,
    entries: list[dict[str, str]],
) -> pd.DataFrame:
    frame = load_field_catalog(path)
    if not entries:
        return frame

    for entry in entries:
        field_key = _normalize_field_value(entry.get("field", ""))
        if not field_key:
            continue
        payload = {
            "field": field_key,
            "category": str(entry.get("category", "")).strip(),
            "description": str(entry.get("description", "")).strip(),
            "alpha_use_cases": str(entry.get("alpha_use_cases", "")).strip(),
            "data_quality_checks": str(entry.get("data_quality_checks", "")).strip(),
            "notes": str(entry.get("notes", "")).strip(),
        }
        if field_key in set(frame["field"]):
            for col, value in payload.items():
                frame.loc[frame["field"] == field_key, col] = value
        else:
            frame = pd.concat([frame, pd.DataFrame([payload])], ignore_index=True)

    frame = frame.sort_values(["category", "field"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame


def load_template_map(path: Path = DEFAULT_TEMPLATE_MAP) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Template map file not found: {path}")
    frame = pd.read_csv(path)
    frame["required_fields_list"] = frame["required_fields"].map(_parse_pipe_list)
    frame["optional_fields_list"] = frame["optional_fields"].map(_parse_pipe_list)
    frame["hypothesis_class"] = frame["hypothesis_class"].astype(str).str.strip()
    return frame


def suggest_templates(
    frame: pd.DataFrame,
    *,
    fields: list[str] | None = None,
    hypothesis_class: str = "",
    limit: int = 20,
) -> pd.DataFrame:
    out = frame.copy()
    if hypothesis_class:
        out = out[
            out["hypothesis_class"].str.lower() == hypothesis_class.strip().lower()
        ]

    available = {f.strip() for f in (fields or []) if f.strip()}

    def compute_missing(required: list[str]) -> list[str]:
        if not available:
            return required
        return [f for f in required if f not in available]

    out["missing_required"] = out["required_fields_list"].map(compute_missing)
    out["required_count"] = out["required_fields_list"].map(len)
    out["missing_count"] = out["missing_required"].map(len)
    out["matched_count"] = out["required_count"] - out["missing_count"]
    out["coverage_ratio"] = out["matched_count"] / out["required_count"].clip(lower=1)
    out["is_feasible_with_fields"] = out["missing_count"] == 0

    out = out.sort_values(
        ["is_feasible_with_fields", "coverage_ratio", "matched_count"],
        ascending=[False, False, False],
    ).head(limit)
    return out.reset_index(drop=True)


def load_settings_profiles(path: Path = DEFAULT_SETTINGS_PROFILES) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Settings profile file not found: {path}")
    frame = pd.read_csv(path)
    frame["profile_id"] = frame["profile_id"].astype(str).str.strip()
    return frame


def get_settings_profile(
    frame: pd.DataFrame, profile_id: str
) -> dict[str, str] | None:
    if not profile_id:
        return None
    matches = frame[frame["profile_id"] == profile_id]
    if matches.empty:
        return None
    return dict(matches.iloc[0].astype(str).to_dict())
