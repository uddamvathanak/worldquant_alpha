from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import uuid

import pandas as pd

from .hypothesis import Hypothesis, HypothesisStore
from .knowledge import (
    DEFAULT_FIELD_CATALOG,
    DEFAULT_SETTINGS_PROFILES,
    DEFAULT_TEMPLATE_MAP,
    get_settings_profile,
    load_field_catalog,
    load_settings_profiles,
    load_template_map,
    query_field_catalog,
    suggest_templates,
    parse_field_entries_text,
    upsert_field_catalog_entry,
    upsert_field_catalog_entries,
)
from .logging_store import ExperimentStore, RunRecord


DEFAULT_HYPOTHESES_PATH = Path("hypotheses/hypotheses.jsonl")
DEFAULT_DB_PATH = Path("logs/experiments.db")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_csv_list(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _format_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if pd.isna(value):
            return ""
        return f"{value:.6g}"
    return str(value)


def _build_settings_snapshot(
    args: argparse.Namespace,
    profile: dict[str, str] | None,
) -> dict[str, object]:
    profile = profile or {}

    def pick(name: str, arg_value: object) -> object:
        if arg_value is None:
            return profile.get(name, "")
        if isinstance(arg_value, str) and not arg_value.strip():
            return profile.get(name, "")
        return arg_value

    settings = {
        "settings_profile": pick("profile_id", args.settings_profile),
        "objective": args.objective,
        "source_platform": args.source_platform,
        "simulation_id": args.simulation_id,
        "region": pick("region", args.region),
        "universe": pick("universe", args.universe),
        "delay": pick("delay", args.delay),
        "decay": pick("decay", args.decay),
        "neutralization": pick("neutralization", args.neutralization),
        "truncation": pick("truncation", args.truncation),
        "pasteurization": pick("pasteurization", args.pasteurization),
        "nan_handling": pick("nan_handling", args.nan_handling),
        "unit_handling": pick("unit_handling", args.unit_handling),
        "test_period": pick("test_period", args.test_period),
        "book_size": pick("book_size", args.book_size),
    }
    return settings


def _extract_fields_from_expression(expression: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression or "")
    return sorted(set(tokens))


def _safe_markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return ""
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    lines = [header, sep]
    for _, row in frame[columns].iterrows():
        values = [str(row[col]).replace("|", "/") for col in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _infer_profile_from_template_id(
    template_map: pd.DataFrame,
    template_id: str,
) -> str:
    if not template_id:
        return ""
    match = template_map[template_map["template_id"] == template_id]
    if match.empty:
        return ""
    return str(match.iloc[0]["default_settings_profile"]).strip()


def _parse_metric_items(metric_items: list[str]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for item in metric_items:
        if "=" not in item:
            raise ValueError(f"Invalid --metric entry: {item}. Expected key=value")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Invalid --metric entry: {item}. Missing key")
        try:
            parsed[key] = float(value)
        except ValueError:
            parsed[key] = value
    return parsed


def _coalesce_numeric(frame: pd.DataFrame, candidates: list[str]) -> pd.Series:
    out = pd.Series([pd.NA] * len(frame), index=frame.index, dtype="object")
    for col in candidates:
        if col in frame.columns:
            out = out.where(out.notna(), frame[col])
    return pd.to_numeric(out, errors="coerce")


def _settings_markdown_lines(settings: dict[str, object]) -> list[str]:
    preferred_order = [
        "settings_profile",
        "region",
        "universe",
        "delay",
        "decay",
        "neutralization",
        "truncation",
        "pasteurization",
        "nan_handling",
        "unit_handling",
        "test_period",
        "book_size",
        "objective",
    ]
    lines: list[str] = []
    for key in preferred_order:
        value = _format_scalar(settings.get(key, ""))
        if value == "":
            continue
        lines.append(f"- `{key}`: `{value}`")
    return lines


def cmd_init(_: argparse.Namespace) -> int:
    for path in [
        Path("hypotheses"),
        Path("logs"),
        Path("reports"),
        Path("docs"),
        Path("knowledge"),
    ]:
        path.mkdir(parents=True, exist_ok=True)
    print("Initialized: hypotheses, logs, reports, docs, knowledge")
    return 0


def cmd_add_hypothesis(args: argparse.Namespace) -> int:
    store = HypothesisStore(Path(args.hypotheses_path))
    tags = _parse_csv_list(args.tags)
    fields_used = _parse_csv_list(args.fields_used)
    hypothesis = store.create(
        title=args.title,
        rationale=args.rationale,
        expression=args.expression,
        market=args.market or "",
        tags=tags,
        fields_used=fields_used,
        template_id=args.template_id or "",
        setting_notes=args.setting_notes or "",
        economic_hypothesis=args.economic_hypothesis or "",
        behavioral_mechanism=args.behavioral_mechanism or "",
        risk_hypothesis=args.risk_hypothesis or "",
        failure_modes=args.failure_modes or "",
    )
    print(f"Hypothesis created: {hypothesis.hypothesis_id}")
    return 0


def cmd_list_hypotheses(args: argparse.Namespace) -> int:
    store = HypothesisStore(Path(args.hypotheses_path))
    items = store.list()
    if not items:
        print("No hypotheses registered.")
        return 0
    frame = pd.DataFrame(
        [
            {
                "hypothesis_id": h.hypothesis_id,
                "created_at": h.created_at,
                "title": h.title,
                "template_id": h.template_id,
                "fields_used": ",".join(h.fields_used),
                "market": h.market,
                "tags": ",".join(h.tags),
                "economic_hypothesis": h.economic_hypothesis,
                "expression": h.expression,
            }
            for h in items
        ]
    )
    print(frame.to_string(index=False))
    return 0


def cmd_annotate_hypothesis(args: argparse.Namespace) -> int:
    store = HypothesisStore(Path(args.hypotheses_path))
    updates: dict[str, str] = {}
    if args.economic_hypothesis:
        updates["economic_hypothesis"] = args.economic_hypothesis
    if args.behavioral_mechanism:
        updates["behavioral_mechanism"] = args.behavioral_mechanism
    if args.risk_hypothesis:
        updates["risk_hypothesis"] = args.risk_hypothesis
    if args.failure_modes:
        updates["failure_modes"] = args.failure_modes
    if args.rationale:
        updates["rationale"] = args.rationale
    if args.setting_notes:
        updates["setting_notes"] = args.setting_notes

    if not updates:
        print(
            "No annotation fields provided. Use --economic-hypothesis, --behavioral-mechanism, --risk-hypothesis, --failure-modes, --rationale, or --setting-notes.",
            file=sys.stderr,
        )
        return 1

    hypothesis = store.update(args.hypothesis_id, **updates)
    if not hypothesis:
        print(f"Hypothesis not found: {args.hypothesis_id}", file=sys.stderr)
        return 1

    print(f"Hypothesis updated: {hypothesis.hypothesis_id}")
    return 0


def cmd_fields(args: argparse.Namespace) -> int:
    frame = load_field_catalog(Path(args.field_catalog_path))
    out = query_field_catalog(
        frame,
        query=args.query or "",
        category=args.category or "",
    )
    if out.empty:
        print("No field matches.")
        return 0
    cols = [
        "field",
        "category",
        "description",
        "alpha_use_cases",
        "data_quality_checks",
        "notes",
    ]
    print(out[cols].to_string(index=False))
    return 0


def cmd_upsert_field(args: argparse.Namespace) -> int:
    upsert_field_catalog_entry(
        Path(args.field_catalog_path),
        field=args.field,
        category=args.category,
        description=args.description,
        alpha_use_cases=args.alpha_use_cases or "",
        data_quality_checks=args.data_quality_checks or "",
        notes=args.notes or "",
    )
    print(f"Field saved: {args.field.strip().lower()}")
    return 0


def cmd_import_fields_text(args: argparse.Namespace) -> int:
    raw_text = args.text or ""
    if args.text_file:
        raw_text = Path(args.text_file).read_text(encoding="utf-8")
    elif not raw_text and not sys.stdin.isatty():
        raw_text = sys.stdin.read()

    if not raw_text.strip():
        print(
            "Provide --text, --text-file, or pipe text via stdin.",
            file=sys.stderr,
        )
        return 1

    entries = parse_field_entries_text(
        raw_text,
        default_category=args.default_category,
    )
    if not entries:
        print(
            "No field entries parsed from text. Expected formats like 'field: description' or key-value blocks.",
            file=sys.stderr,
        )
        return 1

    if args.notes:
        for entry in entries:
            entry["notes"] = args.notes

    preview = pd.DataFrame(entries)[
        [
            "field",
            "category",
            "description",
            "alpha_use_cases",
            "data_quality_checks",
            "notes",
        ]
    ]
    print(preview.to_string(index=False))

    if args.dry_run:
        print(f"Parsed entries (dry-run): {len(entries)}")
        return 0

    upsert_field_catalog_entries(
        Path(args.field_catalog_path),
        entries,
    )
    print(f"Imported fields: {len(entries)}")
    return 0


def cmd_templates(args: argparse.Namespace) -> int:
    frame = load_template_map(Path(args.template_map_path))
    fields = _parse_csv_list(args.fields)
    out = suggest_templates(
        frame,
        fields=fields,
        hypothesis_class=args.hypothesis_class or "",
        limit=args.limit,
    )
    if out.empty:
        print("No templates found.")
        return 0

    out = out.copy()
    out["missing_required"] = out["missing_required"].map(lambda x: ",".join(x))
    display_cols = [
        "template_id",
        "template_name",
        "hypothesis_class",
        "required_fields",
        "missing_required",
        "is_feasible_with_fields",
        "default_settings_profile",
    ]
    if args.show_expression:
        display_cols.append("expression_template")
    print(out[display_cols].to_string(index=False))
    return 0


def cmd_settings_profiles(args: argparse.Namespace) -> int:
    frame = load_settings_profiles(Path(args.settings_profiles_path))
    cols = [
        "profile_id",
        "delay",
        "decay",
        "neutralization",
        "truncation",
        "pasteurization",
        "nan_handling",
        "unit_handling",
        "region",
        "universe",
        "test_period",
        "book_size",
        "intuition",
    ]
    print(frame[cols].to_string(index=False))
    return 0


def _load_hypothesis_for_plan(
    hypothesis_id: str,
    hypotheses_path: Path,
) -> Hypothesis | None:
    store = HypothesisStore(hypotheses_path)
    return store.get(hypothesis_id)


def cmd_plan_hypothesis(args: argparse.Namespace) -> int:
    hypothesis = _load_hypothesis_for_plan(
        hypothesis_id=args.hypothesis_id,
        hypotheses_path=Path(args.hypotheses_path),
    )
    if not hypothesis:
        print(f"Hypothesis not found: {args.hypothesis_id}", file=sys.stderr)
        return 1

    field_frame = load_field_catalog(Path(args.field_catalog_path))
    template_frame = load_template_map(Path(args.template_map_path))
    settings_frame = load_settings_profiles(Path(args.settings_profiles_path))

    fields = sorted(set(hypothesis.fields_used + _parse_csv_list(args.fields)))
    if args.infer_fields:
        known_fields = set(field_frame["field"].tolist())
        inferred = [
            token
            for token in _extract_fields_from_expression(hypothesis.expression)
            if token in known_fields
        ]
        fields = sorted(set(fields + inferred))

    field_links = field_frame[field_frame["field"].isin(fields)].copy()
    missing_fields = sorted(set(fields) - set(field_links["field"]))

    hypothesis_class = args.hypothesis_class or ""
    candidates = suggest_templates(
        template_frame,
        fields=fields,
        hypothesis_class=hypothesis_class,
        limit=args.limit,
    )
    if hypothesis.template_id:
        selected = template_frame[template_frame["template_id"] == hypothesis.template_id]
        if not selected.empty:
            selected = selected.copy()
            selected["required_fields_list"] = selected["required_fields"].map(
                lambda x: [v.strip() for v in str(x).split("|") if v.strip()]
            )
            selected["optional_fields_list"] = selected["optional_fields"].map(
                lambda x: [v.strip() for v in str(x).split("|") if v.strip()]
            )
            selected["missing_required"] = selected["required_fields_list"].map(
                lambda req: [f for f in req if f not in set(fields)]
            )
            selected["is_feasible_with_fields"] = selected["missing_required"].map(
                lambda m: len(m) == 0
            )
            candidates = pd.concat([selected, candidates], ignore_index=True)
            candidates = candidates.drop_duplicates(subset=["template_id"], keep="first")

    profile_ids: list[str] = []
    if args.settings_profile:
        profile_ids.append(args.settings_profile)
    if not profile_ids and not candidates.empty and "default_settings_profile" in candidates:
        profile_ids.extend(
            [
                str(v)
                for v in candidates["default_settings_profile"].dropna().tolist()
                if str(v).strip()
            ]
        )
    profile_ids = list(dict.fromkeys(profile_ids))[:3]
    profile_rows = settings_frame[settings_frame["profile_id"].isin(profile_ids)].copy()

    output_path = (
        Path(args.output)
        if args.output
        else Path("reports") / f"{hypothesis.hypothesis_id}_alpha_plan.md"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append(f"# Alpha Plan: {hypothesis.title}")
    lines.append("")
    lines.append(f"- Generated at: {_utc_now_iso()}")
    lines.append(f"- Hypothesis ID: `{hypothesis.hypothesis_id}`")
    lines.append(f"- Expression: `{hypothesis.expression}`")
    lines.append(f"- Template ID: `{hypothesis.template_id or 'N/A'}`")
    lines.append(f"- Fields Used: `{', '.join(fields) if fields else 'N/A'}`")
    lines.append("")
    lines.append("## Rationale")
    lines.append("")
    lines.append(hypothesis.rationale)
    lines.append("")
    lines.append("## Economic Hypothesis")
    lines.append("")
    lines.append(hypothesis.economic_hypothesis or "Not documented yet.")
    lines.append("")
    lines.append("## Behavioral Mechanism")
    lines.append("")
    lines.append(hypothesis.behavioral_mechanism or "Not documented yet.")
    lines.append("")
    lines.append("## Risk Hypothesis")
    lines.append("")
    lines.append(hypothesis.risk_hypothesis or "Not documented yet.")
    lines.append("")
    lines.append("## Failure Modes")
    lines.append("")
    lines.append(hypothesis.failure_modes or "Not documented yet.")
    lines.append("")
    lines.append("## Field Encyclopedia Links")
    lines.append("")
    if field_links.empty:
        lines.append("No known fields linked. Add `--fields-used` to hypothesis and rerun plan.")
    else:
        table = _safe_markdown_table(
            field_links,
            [
                "field",
                "category",
                "description",
                "alpha_use_cases",
                "data_quality_checks",
            ],
        )
        lines.append(table)
    if missing_fields:
        lines.append("")
        lines.append(f"Missing field docs: `{', '.join(missing_fields)}`")
    lines.append("")
    lines.append("## Candidate Templates")
    lines.append("")
    if candidates.empty:
        lines.append("No template candidates matched.")
    else:
        candidates = candidates.copy()
        candidates["missing_required"] = candidates["missing_required"].map(
            lambda x: ",".join(x) if isinstance(x, list) else str(x)
        )
        table = _safe_markdown_table(
            candidates,
            [
                "template_id",
                "template_name",
                "hypothesis_class",
                "required_fields",
                "missing_required",
                "is_feasible_with_fields",
                "default_settings_profile",
            ],
        )
        lines.append(table)
        lines.append("")
        lines.append("Top expression templates:")
        for _, row in candidates.head(5).iterrows():
            lines.append(f"- `{row['template_id']}`: `{row['expression_template']}`")
    lines.append("")
    lines.append("## Recommended Settings Profiles")
    lines.append("")
    if profile_rows.empty:
        lines.append("No profile selected. Use `--settings-profile` or link a template with default profile.")
    else:
        table = _safe_markdown_table(
            profile_rows,
            [
                "profile_id",
                "delay",
                "decay",
                "neutralization",
                "truncation",
                "pasteurization",
                "nan_handling",
                "unit_handling",
                "region",
                "universe",
                "test_period",
                "book_size",
                "intuition",
            ],
        )
        lines.append(table)
    lines.append("")
    lines.append("## Experiment Checklist")
    lines.append("")
    lines.append("- Keep settings fixed while testing expression variants.")
    lines.append("- Log platform metrics with `wqa log-result`.")
    lines.append("- Prioritize higher fitness and margin, then verify robustness.")
    lines.append("- Write why it worked/failed and economic intuition for each run.")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Plan file: {output_path}")
    return 0


def cmd_propose_run(args: argparse.Namespace) -> int:
    store = HypothesisStore(Path(args.hypotheses_path))
    tags = _parse_csv_list(args.tags)
    fields_used = _parse_csv_list(args.fields_used)
    hypothesis = store.create(
        title=args.title,
        rationale=args.rationale,
        expression=args.expression,
        market=args.market or "",
        tags=tags,
        fields_used=fields_used,
        template_id=args.template_id or "",
        setting_notes=args.setting_notes or "",
        economic_hypothesis=args.economic_hypothesis or "",
        behavioral_mechanism=args.behavioral_mechanism or "",
        risk_hypothesis=args.risk_hypothesis or "",
        failure_modes=args.failure_modes or "",
    )

    selected_profile_id = args.settings_profile
    if not selected_profile_id and hypothesis.template_id:
        template_map = load_template_map(Path(args.template_map_path))
        selected_profile_id = _infer_profile_from_template_id(
            template_map,
            hypothesis.template_id,
        )
    if not selected_profile_id:
        selected_profile_id = "baseline_d1"

    settings_profiles = load_settings_profiles(Path(args.settings_profiles_path))
    profile_dict = get_settings_profile(settings_profiles, selected_profile_id)
    if not profile_dict:
        print(f"Settings profile not found: {selected_profile_id}", file=sys.stderr)
        return 1

    args.settings_profile = selected_profile_id
    settings = _build_settings_snapshot(args, profile_dict)

    output_path = (
        Path(args.output)
        if args.output
        else Path("reports") / f"{hypothesis.hypothesis_id}_simulation_brief.md"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    log_command = (
        "wqa log-result "
        f"--hypothesis-id {hypothesis.hypothesis_id} "
        '--simulation-id "<brain-sim-id>" '
        f"--settings-profile {selected_profile_id} "
        "--fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> "
        '--status candidate --why-worked "<why worked>" --why-failed "<why failed>" '
        '--economic-intuition "<economic story>" --next-step "<next test>"'
    )

    lines: list[str] = []
    lines.append(f"# Simulation Brief: {hypothesis.title}")
    lines.append("")
    lines.append(f"- Generated at: {_utc_now_iso()}")
    lines.append(f"- Hypothesis ID: `{hypothesis.hypothesis_id}`")
    lines.append(f"- Market: `{hypothesis.market or 'N/A'}`")
    lines.append(f"- Template ID: `{hypothesis.template_id or 'N/A'}`")
    lines.append(f"- Fields Used: `{', '.join(hypothesis.fields_used) if hypothesis.fields_used else 'N/A'}`")
    lines.append("")
    lines.append("## Hypothesis")
    lines.append("")
    lines.append(hypothesis.rationale)
    lines.append("")
    lines.append("## Economic Hypothesis")
    lines.append("")
    lines.append(hypothesis.economic_hypothesis or "Not documented yet.")
    lines.append("")
    lines.append("## Behavioral Mechanism")
    lines.append("")
    lines.append(hypothesis.behavioral_mechanism or "Not documented yet.")
    lines.append("")
    lines.append("## Risk Hypothesis")
    lines.append("")
    lines.append(hypothesis.risk_hypothesis or "Not documented yet.")
    lines.append("")
    lines.append("## Failure Modes")
    lines.append("")
    lines.append(hypothesis.failure_modes or "Not documented yet.")
    lines.append("")
    lines.append("## WorldQuant Expression")
    lines.append("")
    lines.append("```text")
    lines.append(hypothesis.expression)
    lines.append("```")
    lines.append("")
    lines.append("## Simulation Settings")
    lines.append("")
    lines.extend(_settings_markdown_lines(settings))
    lines.append("")
    lines.append("## After You Run Simulation")
    lines.append("")
    lines.append("Paste returned metrics with this command:")
    lines.append("")
    lines.append("```bash")
    lines.append(log_command)
    lines.append("```")
    lines.append("")
    lines.append("Required minimum metrics to send back:")
    lines.append("- `fitness`")
    lines.append("- `margin`")
    lines.append("- `sharpe`")
    lines.append("- `turnover`")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Hypothesis created: {hypothesis.hypothesis_id}")
    print(f"Simulation brief: {output_path}")
    print("WorldQuant expression:")
    print(hypothesis.expression)
    print("Settings profile:")
    print(selected_profile_id)
    return 0


def cmd_log_result(args: argparse.Namespace) -> int:
    hypothesis_id = args.hypothesis_id.strip()
    title = args.title.strip()
    expression = args.expression.strip()
    hypothesis: Hypothesis | None = None

    if hypothesis_id:
        store = HypothesisStore(Path(args.hypotheses_path))
        hypothesis = store.get(hypothesis_id)
        if not hypothesis:
            print(f"Hypothesis not found: {hypothesis_id}", file=sys.stderr)
            return 1
        if not title:
            title = hypothesis.title
        if not expression:
            expression = hypothesis.expression
    elif not title:
        print("Provide --hypothesis-id or --title", file=sys.stderr)
        return 1

    template_map = load_template_map(Path(args.template_map_path))
    inferred_profile_id = ""
    if not args.settings_profile and hypothesis and hypothesis.template_id:
        inferred_profile_id = _infer_profile_from_template_id(
            template_map,
            hypothesis.template_id,
        )

    selected_profile_id = args.settings_profile or inferred_profile_id
    profile_dict: dict[str, str] | None = None
    if selected_profile_id:
        settings_profiles = load_settings_profiles(Path(args.settings_profiles_path))
        profile_dict = get_settings_profile(settings_profiles, selected_profile_id)
        if not profile_dict:
            print(f"Settings profile not found: {selected_profile_id}", file=sys.stderr)
            return 1
        args.settings_profile = selected_profile_id

    settings = _build_settings_snapshot(args, profile_dict)

    metrics: dict[str, object] = {}
    if args.fitness is not None:
        metrics["fitness"] = float(args.fitness)
    if args.margin is not None:
        metrics["margin"] = float(args.margin)
    if args.sharpe is not None:
        metrics["sharpe"] = float(args.sharpe)
    if args.ic is not None:
        metrics["ic"] = float(args.ic)
    if args.turnover is not None:
        metrics["turnover"] = float(args.turnover)
    if args.max_drawdown is not None:
        metrics["max_drawdown"] = float(args.max_drawdown)
    if args.returns is not None:
        metrics["returns"] = float(args.returns)
    metrics.update(_parse_metric_items(args.metric or []))

    if not metrics:
        print("Provide at least one metric. Example: --fitness 1.2 --margin 30", file=sys.stderr)
        return 1

    run_id = uuid.uuid4().hex[:12]
    dataset_name = args.dataset.strip() or args.simulation_id.strip() or "external_result"

    record = RunRecord(
        run_id=run_id,
        run_at=_utc_now_iso(),
        hypothesis_id=hypothesis_id or None,
        title=title or None,
        expression=expression,
        dataset=dataset_name,
        notes=args.notes or "",
        metrics=metrics,
        settings=settings,
        status=args.status,
        why_worked=args.why_worked or "",
        why_failed=args.why_failed or "",
        economic_intuition=args.economic_intuition or "",
        next_step=args.next_step or "",
    )

    db = ExperimentStore(Path(args.db_path))
    db.log_run(record)

    print(f"Result logged: {run_id}")
    print("metrics:")
    for key in sorted(metrics.keys()):
        print(f"  {key}: {_format_scalar(metrics[key])}")
    print("settings_snapshot:")
    for key in sorted(settings.keys()):
        print(f"  {key}: {_format_scalar(settings[key])}")
    return 0


def cmd_leaderboard(args: argparse.Namespace) -> int:
    db = ExperimentStore(Path(args.db_path))
    runs = db.list_runs(limit=max(args.limit * 20, args.limit))
    if runs.empty:
        print("No logged results found.")
        return 0

    if not args.include_legacy and "status" in runs.columns:
        runs = runs[runs["status"].astype(str).str.strip().str.len() > 0]
        if runs.empty:
            print("No non-legacy logged results found.")
            return 0

    if args.settings_profile and "setting_settings_profile" in runs.columns:
        runs = runs[runs["setting_settings_profile"] == args.settings_profile]
        if runs.empty:
            print(f"No results for settings profile: {args.settings_profile}")
            return 0

    if args.status and "status" in runs.columns:
        runs = runs[runs["status"] == args.status]
        if runs.empty:
            print(f"No results for status: {args.status}")
            return 0

    runs = runs.copy()
    runs["fitness_score"] = _coalesce_numeric(
        runs,
        ["metric_fitness", "metric_fitness_proxy"],
    )
    runs["margin_score"] = _coalesce_numeric(
        runs,
        ["metric_margin", "metric_margin_proxy_bps"],
    )
    runs["sharpe_score"] = _coalesce_numeric(runs, ["metric_sharpe"])
    runs["ic_score"] = _coalesce_numeric(runs, ["metric_ic", "metric_ic_mean"])
    runs["turnover_score"] = _coalesce_numeric(
        runs,
        ["metric_turnover", "metric_turnover_mean"],
    )
    runs["drawdown_score"] = _coalesce_numeric(
        runs,
        ["metric_max_drawdown"],
    )

    sort_map = {
        "fitness": ["fitness_score", "margin_score", "sharpe_score"],
        "margin": ["margin_score", "fitness_score", "sharpe_score"],
        "sharpe": ["sharpe_score", "fitness_score"],
        "ic": ["ic_score", "fitness_score"],
        "recent": ["run_at"],
    }
    sort_cols = [col for col in sort_map[args.sort_by] if col in runs.columns]
    ascending = [False] * len(sort_cols)
    if args.sort_by == "recent":
        ascending = [False]
    runs = runs.sort_values(sort_cols, ascending=ascending)
    runs = runs.head(args.limit)

    display_columns = [
        col
        for col in [
            "run_id",
            "run_at",
            "hypothesis_id",
            "title",
            "dataset",
            "status",
            "fitness_score",
            "margin_score",
            "sharpe_score",
            "ic_score",
            "turnover_score",
            "drawdown_score",
            "next_step",
        ]
        if col in runs.columns
    ]
    print(runs[display_columns].to_string(index=False))
    return 0


def cmd_show_run(args: argparse.Namespace) -> int:
    db = ExperimentStore(Path(args.db_path))
    record = db.get_run(args.run_id)
    if not record:
        print(f"Run not found: {args.run_id}", file=sys.stderr)
        return 1

    print(f"run_id: {record['run_id']}")
    print(f"run_at: {record['run_at']}")
    print(f"hypothesis_id: {record.get('hypothesis_id') or ''}")
    print(f"title: {record.get('title') or ''}")
    print(f"expression: {record.get('expression') or ''}")
    print(f"dataset: {record.get('dataset') or ''}")
    print(f"status: {record.get('status') or ''}")
    print(f"notes: {record.get('notes') or ''}")
    print(f"why_worked: {record.get('why_worked') or ''}")
    print(f"why_failed: {record.get('why_failed') or ''}")
    print(f"economic_intuition: {record.get('economic_intuition') or ''}")
    print(f"next_step: {record.get('next_step') or ''}")
    print("metrics:")
    for key, value in sorted(record.get("metrics", {}).items()):
        print(f"  {key}: {_format_scalar(value)}")
    print("settings:")
    for key, value in sorted(record.get("settings", {}).items()):
        print(f"  {key}: {_format_scalar(value)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wqa",
        description="Metadata-first alpha research knowledge and result logging.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init = subparsers.add_parser("init", help="Create default folders.")
    p_init.set_defaults(func=cmd_init)

    p_add = subparsers.add_parser("add-hypothesis", help="Add a hypothesis to registry.")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--rationale", required=True)
    p_add.add_argument("--expression", required=True)
    p_add.add_argument("--market", default="")
    p_add.add_argument("--tags", default="")
    p_add.add_argument("--fields-used", default="")
    p_add.add_argument("--template-id", default="")
    p_add.add_argument("--setting-notes", default="")
    p_add.add_argument("--economic-hypothesis", default="")
    p_add.add_argument("--behavioral-mechanism", default="")
    p_add.add_argument("--risk-hypothesis", default="")
    p_add.add_argument("--failure-modes", default="")
    p_add.add_argument("--hypotheses-path", default=str(DEFAULT_HYPOTHESES_PATH))
    p_add.set_defaults(func=cmd_add_hypothesis)

    p_list = subparsers.add_parser("list-hypotheses", help="List hypotheses.")
    p_list.add_argument("--hypotheses-path", default=str(DEFAULT_HYPOTHESES_PATH))
    p_list.set_defaults(func=cmd_list_hypotheses)

    p_annotate = subparsers.add_parser(
        "annotate-hypothesis",
        help="Add or update economic/risk/failure annotations for a hypothesis.",
    )
    p_annotate.add_argument("--hypothesis-id", required=True)
    p_annotate.add_argument("--economic-hypothesis", default="")
    p_annotate.add_argument("--behavioral-mechanism", default="")
    p_annotate.add_argument("--risk-hypothesis", default="")
    p_annotate.add_argument("--failure-modes", default="")
    p_annotate.add_argument("--rationale", default="")
    p_annotate.add_argument("--setting-notes", default="")
    p_annotate.add_argument("--hypotheses-path", default=str(DEFAULT_HYPOTHESES_PATH))
    p_annotate.set_defaults(func=cmd_annotate_hypothesis)

    p_fields = subparsers.add_parser("fields", help="Query field encyclopedia.")
    p_fields.add_argument("--query", default="")
    p_fields.add_argument("--category", default="")
    p_fields.add_argument("--field-catalog-path", default=str(DEFAULT_FIELD_CATALOG))
    p_fields.set_defaults(func=cmd_fields)

    p_upsert_field = subparsers.add_parser(
        "upsert-field",
        help="Add or update a field encyclopedia entry.",
    )
    p_upsert_field.add_argument("--field", required=True)
    p_upsert_field.add_argument("--category", required=True)
    p_upsert_field.add_argument("--description", required=True)
    p_upsert_field.add_argument("--alpha-use-cases", default="")
    p_upsert_field.add_argument("--data-quality-checks", default="")
    p_upsert_field.add_argument("--notes", default="")
    p_upsert_field.add_argument(
        "--field-catalog-path",
        default=str(DEFAULT_FIELD_CATALOG),
    )
    p_upsert_field.set_defaults(func=cmd_upsert_field)

    p_import_fields = subparsers.add_parser(
        "import-fields-text",
        help="Parse pasted field text and upsert entries into encyclopedia.",
    )
    p_import_fields.add_argument("--text", default="")
    p_import_fields.add_argument("--text-file", default="")
    p_import_fields.add_argument("--default-category", default="Unknown")
    p_import_fields.add_argument("--notes", default="")
    p_import_fields.add_argument("--dry-run", action="store_true")
    p_import_fields.add_argument(
        "--field-catalog-path",
        default=str(DEFAULT_FIELD_CATALOG),
    )
    p_import_fields.set_defaults(func=cmd_import_fields_text)

    p_templates = subparsers.add_parser("templates", help="Suggest templates from fields.")
    p_templates.add_argument("--fields", default="")
    p_templates.add_argument("--hypothesis-class", default="")
    p_templates.add_argument("--limit", type=int, default=20)
    p_templates.add_argument("--show-expression", action="store_true")
    p_templates.add_argument("--template-map-path", default=str(DEFAULT_TEMPLATE_MAP))
    p_templates.set_defaults(func=cmd_templates)

    p_profiles = subparsers.add_parser(
        "settings-profiles",
        help="List default settings profiles.",
    )
    p_profiles.add_argument(
        "--settings-profiles-path",
        default=str(DEFAULT_SETTINGS_PROFILES),
    )
    p_profiles.set_defaults(func=cmd_settings_profiles)

    p_plan = subparsers.add_parser(
        "plan-hypothesis",
        help="Create markdown plan linking fields, templates, and settings.",
    )
    p_plan.add_argument("--hypothesis-id", required=True)
    p_plan.add_argument("--fields", default="")
    p_plan.add_argument("--hypothesis-class", default="")
    p_plan.add_argument("--settings-profile", default="")
    p_plan.add_argument("--limit", type=int, default=10)
    p_plan.add_argument("--infer-fields", action="store_true")
    p_plan.add_argument("--output", default="")
    p_plan.add_argument("--hypotheses-path", default=str(DEFAULT_HYPOTHESES_PATH))
    p_plan.add_argument("--field-catalog-path", default=str(DEFAULT_FIELD_CATALOG))
    p_plan.add_argument("--template-map-path", default=str(DEFAULT_TEMPLATE_MAP))
    p_plan.add_argument("--settings-profiles-path", default=str(DEFAULT_SETTINGS_PROFILES))
    p_plan.set_defaults(func=cmd_plan_hypothesis)

    p_propose = subparsers.add_parser(
        "propose-run",
        help="Create hypothesis and a simulation brief with consistent settings.",
    )
    p_propose.add_argument("--title", required=True)
    p_propose.add_argument("--rationale", required=True)
    p_propose.add_argument("--expression", required=True)
    p_propose.add_argument("--market", default="")
    p_propose.add_argument("--tags", default="")
    p_propose.add_argument("--fields-used", default="")
    p_propose.add_argument("--template-id", default="")
    p_propose.add_argument("--setting-notes", default="")
    p_propose.add_argument("--economic-hypothesis", default="")
    p_propose.add_argument("--behavioral-mechanism", default="")
    p_propose.add_argument("--risk-hypothesis", default="")
    p_propose.add_argument("--failure-modes", default="")

    p_propose.add_argument("--objective", default="fitness_margin")
    p_propose.add_argument("--source-platform", default="WorldQuant BRAIN")
    p_propose.add_argument("--simulation-id", default="")
    p_propose.add_argument("--settings-profile", default="")
    p_propose.add_argument("--region", default="")
    p_propose.add_argument("--universe", default="")
    p_propose.add_argument("--delay", type=int, default=None)
    p_propose.add_argument("--decay", type=int, default=None)
    p_propose.add_argument("--neutralization", default="")
    p_propose.add_argument("--truncation", type=float, default=None)
    p_propose.add_argument("--pasteurization", default="")
    p_propose.add_argument("--nan-handling", default="")
    p_propose.add_argument("--unit-handling", default="")
    p_propose.add_argument("--test-period", default="")
    p_propose.add_argument("--book-size", type=float, default=None)

    p_propose.add_argument("--hypotheses-path", default=str(DEFAULT_HYPOTHESES_PATH))
    p_propose.add_argument("--template-map-path", default=str(DEFAULT_TEMPLATE_MAP))
    p_propose.add_argument("--settings-profiles-path", default=str(DEFAULT_SETTINGS_PROFILES))
    p_propose.add_argument("--output", default="")
    p_propose.set_defaults(func=cmd_propose_run)

    p_log = subparsers.add_parser(
        "log-result",
        help="Log external platform metrics and qualitative notes.",
    )
    p_log.add_argument("--hypothesis-id", default="")
    p_log.add_argument("--title", default="")
    p_log.add_argument("--expression", default="")
    p_log.add_argument("--dataset", default="")
    p_log.add_argument("--notes", default="")
    p_log.add_argument("--status", default="candidate", choices=["candidate", "keep", "watch", "reject"])
    p_log.add_argument("--why-worked", default="")
    p_log.add_argument("--why-failed", default="")
    p_log.add_argument("--economic-intuition", default="")
    p_log.add_argument("--next-step", default="")

    p_log.add_argument("--fitness", type=float, default=None)
    p_log.add_argument("--margin", type=float, default=None)
    p_log.add_argument("--sharpe", type=float, default=None)
    p_log.add_argument("--ic", type=float, default=None)
    p_log.add_argument("--turnover", type=float, default=None)
    p_log.add_argument("--max-drawdown", type=float, default=None)
    p_log.add_argument("--returns", type=float, default=None)
    p_log.add_argument("--metric", action="append", default=[])

    p_log.add_argument("--objective", default="fitness_margin")
    p_log.add_argument("--source-platform", default="WorldQuant BRAIN")
    p_log.add_argument("--simulation-id", default="")
    p_log.add_argument("--settings-profile", default="")
    p_log.add_argument("--region", default="")
    p_log.add_argument("--universe", default="")
    p_log.add_argument("--delay", type=int, default=None)
    p_log.add_argument("--decay", type=int, default=None)
    p_log.add_argument("--neutralization", default="")
    p_log.add_argument("--truncation", type=float, default=None)
    p_log.add_argument("--pasteurization", default="")
    p_log.add_argument("--nan-handling", default="")
    p_log.add_argument("--unit-handling", default="")
    p_log.add_argument("--test-period", default="")
    p_log.add_argument("--book-size", type=float, default=None)

    p_log.add_argument("--hypotheses-path", default=str(DEFAULT_HYPOTHESES_PATH))
    p_log.add_argument("--template-map-path", default=str(DEFAULT_TEMPLATE_MAP))
    p_log.add_argument("--settings-profiles-path", default=str(DEFAULT_SETTINGS_PROFILES))
    p_log.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    p_log.set_defaults(func=cmd_log_result)

    p_lead = subparsers.add_parser("leaderboard", help="Show top logged results.")
    p_lead.add_argument("--limit", default=20, type=int)
    p_lead.add_argument(
        "--sort-by",
        default="fitness",
        choices=["fitness", "margin", "sharpe", "ic", "recent"],
    )
    p_lead.add_argument("--settings-profile", default="")
    p_lead.add_argument("--status", default="")
    p_lead.add_argument("--include-legacy", action="store_true")
    p_lead.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    p_lead.set_defaults(func=cmd_leaderboard)

    p_show = subparsers.add_parser("show-run", help="Show full details for one logged run.")
    p_show.add_argument("--run-id", required=True)
    p_show.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    p_show.set_defaults(func=cmd_show_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
