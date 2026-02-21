# Field Encyclopedia Guide

The field encyclopedia is the research memory layer that prevents repeating weak ideas and helps generate better hypotheses.

Source file:
- `knowledge/field_encyclopedia.csv`

## Required Columns

- `field`
- `category`
- `description`
- `alpha_use_cases`
- `data_quality_checks`
- `notes`

## Usage

Search quickly:
```bash
wqa fields --query volume
```

Filter category:
```bash
wqa fields --category Risk
```

Accepted text formats for `wqa import-fields-text`:
- `field: description`
- `field - description`
- `field (Category): description`
- key-value blocks:
  - `Field: ...`
  - `Category: ...`
  - `Description: ...`
  - optional `Use Cases: ...`, `Data Quality Checks: ...`, `Notes: ...`

Add or update one field from pasted notes:
```bash
wqa upsert-field --field vwap --category Price --description "Volume weighted average transaction price" --alpha-use-cases "Close-vs-flow dislocation" --data-quality-checks "Check price-volume consistency" --notes "Good for short-horizon mean reversion"
```

Import multiple fields from raw pasted text:
```bash
@'
vwap: Volume weighted average transaction price
close: Session closing price
volume: Traded shares in session
'@ | wqa import-fields-text --default-category Price --notes "pasted from BRAIN"
```

## How to Extend

When you discover a useful new field:
1. Add/update via `wqa upsert-field`, `wqa import-fields-text`, or edit `knowledge/field_encyclopedia.csv`.
2. Add at least one use case and one quality check.
3. Link it to one or more template rows in `knowledge/alpha_template_map.csv`.
4. Use it in `--fields-used` when adding hypotheses.

## Quality Standard

Each field row should answer:
- What information does this field carry?
- How is it used in alpha construction?
- What can go wrong in the data?
- How do we detect and mitigate that issue?
