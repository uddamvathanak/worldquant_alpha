from pathlib import Path

from worldquant_alpha.knowledge import (
    load_field_catalog,
    load_template_map,
    parse_field_entries_text,
    query_field_catalog,
    suggest_templates,
    upsert_field_catalog_entry,
)


def test_field_catalog_query() -> None:
    frame = load_field_catalog(Path("knowledge/field_encyclopedia.csv"))
    out = query_field_catalog(frame, query="vwap")
    assert not out.empty
    assert "vwap" in out["field"].tolist()


def test_template_suggestions_with_fields() -> None:
    frame = load_template_map(Path("knowledge/alpha_template_map.csv"))
    out = suggest_templates(
        frame,
        fields=["vwap", "close", "volume"],
        hypothesis_class="MeanReversion",
        limit=5,
    )
    assert not out.empty
    assert bool(out.iloc[0]["is_feasible_with_fields"]) is True


def test_upsert_field_catalog_entry(tmp_path: Path) -> None:
    catalog_path = tmp_path / "fields.csv"
    upsert_field_catalog_entry(
        catalog_path,
        field="custom_field",
        category="Custom",
        description="First version",
        alpha_use_cases="Use in hypothesis A",
        data_quality_checks="Check missing values",
        notes="initial",
    )
    upsert_field_catalog_entry(
        catalog_path,
        field="custom_field",
        category="Custom",
        description="Updated version",
        alpha_use_cases="Use in hypothesis B",
        data_quality_checks="Check update path",
        notes="updated",
    )
    frame = load_field_catalog(catalog_path)
    row = frame[frame["field"] == "custom_field"].iloc[0]
    assert row["description"] == "Updated version"
    assert row["notes"] == "updated"


def test_parse_field_entries_text_line_formats() -> None:
    raw = "\n".join(
        [
            "vwap: Volume weighted average price",
            "close - Closing price",
            "volume (Liquidity): Shares traded in session",
        ]
    )
    entries = parse_field_entries_text(raw, default_category="Unknown")
    names = {entry["field"] for entry in entries}
    assert "vwap" in names
    assert "close" in names
    assert "volume" in names


def test_parse_field_entries_text_key_value_blocks() -> None:
    raw = "\n".join(
        [
            "Field: analyst_revision_30d",
            "Category: Estimate",
            "Description: Net estimate revision over last 30 days",
            "Use Cases: Expectation momentum",
            "",
            "Field: short_interest",
            "Category: Sentiment",
            "Description: Short interest level",
            "Data Quality Checks: Verify reporting lag",
        ]
    )
    entries = parse_field_entries_text(raw, default_category="Unknown")
    assert len(entries) == 2
    assert entries[0]["field"] == "analyst_revision_30d"
    assert entries[0]["category"] == "Estimate"
    assert entries[1]["field"] == "short_interest"
