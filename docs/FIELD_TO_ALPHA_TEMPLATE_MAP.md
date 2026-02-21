# Field to Alpha Template Map

Quick reference for converting available fields into candidate Fast Expressions.

Canonical machine-readable source:
- `knowledge/alpha_template_map.csv`

## Templates

| Template ID | Hypothesis Class | Required Fields | Fast Expression Template | Default Settings Profile |
|---|---|---|---|---|
| `TPL_REV_001` | MeanReversion | `vwap, close` | `rank(vwap/close)` | `baseline_d1` |
| `TPL_REV_002` | MeanReversion | `close` | `-rank(ts_delta(close,1))` | `baseline_d1` |
| `TPL_MOM_001` | Momentum | `close` | `-rank(ts_delta(close,5))` | `baseline_d1` |
| `TPL_VOL_001` | Momentum | `close, returns` | `rank(ts_delta(close,10)/ts_std_dev(returns,20))` | `baseline_d1` |
| `TPL_LIQ_001` | MeanReversion | `volume, adv20` | `rank(ts_delta(volume,1)/adv20)` | `baseline_d1` |
| `TPL_VAL_001` | Value | `book_value_per_share, close` | `rank(book_value_per_share/close)` | `slow_value_d1` |
| `TPL_QUAL_001` | Quality | `eps_ttm, sales_ttm, close` | `rank((eps_ttm/sales_ttm)*ts_delta(close,20))` | `slow_value_d1` |
| `TPL_SENT_001` | Sentiment | `analyst_revision_30d` | `rank(analyst_revision_30d)` | `baseline_d1` |
| `TPL_CROWD_001` | Sentiment | `short_interest, close` | `rank(-short_interest*ts_delta(close,5))` | `baseline_d1` |

## Selection Rules

1. If required fields are not available, do not force-fit the template.
2. If multiple templates fit, test simplest expression first.
3. Keep the settings profile fixed for template comparisons.
4. Promote template only if `fitness` and `margin` both improve.

## CLI Shortcuts

```bash
wqa templates --fields vwap,close,volume --hypothesis-class MeanReversion --show-expression
```

```bash
wqa plan-hypothesis --hypothesis-id <HYPOTHESIS_ID> --infer-fields
```
