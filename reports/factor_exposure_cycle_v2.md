# Factor Exposure Cycle v2 (Pass + Decorr)

This file is the execution tracker for Submittable Alpha Cycle v2.

## Locked Settings (Must Stay Constant)

- Instrument: `Equity`
- Region: `USA`
- Universe: `TOP3000`
- Language: `Fast Expression`
- Delay: `1`
- Decay: `20`
- Neutralization: `Subindustry`
- Truncation: `0.05`
- Pasteurization: `On`
- NaN Handling: `Off`
- Unit Handling: `Verify`
- Max Trade / Max Position: `OFF`
- Settings profile: `usa_top3000_consistency_d20_v1`

## Cycle Policy

- Objective: pass gate while reducing crowding.
- Hard correlation cap: `max_correlation <= 0.65`.
- Family policy: no pure options-core candidate; options sidecar only (`<= 25%` weight).
- Batch shape: `2 pure + 2 hybrid`.
- Fallback policy: if none pass, run exactly one controlled mutation on the top candidate.

## Candidate Registry

| Candidate | Hypothesis ID | Family | Source | Brief |
|---|---|---|---|---|
| P1 | `06b961d2f18f` | pure_family | source_web | `reports/P1_simulation_brief.md` |
| P2 | `85069043edb5` | pure_family | source_web | `reports/P2_simulation_brief.md` |
| H1 | `e6b513f87011` | hybrid_family | source_web | `reports/H1_simulation_brief.md` |
| H2 | `9c9fc53e9595` | hybrid_family | source_web | `reports/H2_simulation_brief.md` |

## Compile / Fallback Rules

1. Analyst field fallback (only if needed):

```text
rev = rank(ts_zscore(ts_backfill(anl4_fcf_flag, 120), 63));
```

2. If any candidate fails due unknown field/operator:
- replace only the failing leg with nearest validated motif.
- keep all settings unchanged.
- log fallback explicitly (`fallback_used=<leg_name>`).

## Required Metrics and Notes Per Run

### Numeric keys
- `fitness`
- `margin`
- `sharpe`
- `turnover`
- `max_drawdown`
- `self_correlation`
- `max_correlation`
- `min_correlation`

### Qualitative keys
- `why_worked`
- `why_failed`
- `economic_intuition`
- `next_step`

### Provenance keys (in notes)
- `source_ref`
- `motif`
- `fallback_used`

## Copy-Ready Log Commands

```bash
wqa log-result --hypothesis-id 06b961d2f18f --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=https://arxiv.org/abs/1601.00991;motif=price_volume_corr;fallback_used=none"

wqa log-result --hypothesis-id 85069043edb5 --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=https://arxiv.org/abs/1601.00991;motif=news_regime_revision;fallback_used=none"

wqa log-result --hypothesis-id e6b513f87011 --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=https://arxiv.org/abs/1601.00991;motif=value_revision_with_small_options;fallback_used=none"

wqa log-result --hypothesis-id 9c9fc53e9595 --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=https://arxiv.org/abs/1601.00991;motif=volume_news_with_small_vrp;fallback_used=none"
```

## Ranking and Selection Rules

1. Rank by: `fitness` -> `margin` -> `sharpe`.
2. Enforce constraints:
- keep only if `fitness >= 1.5`
- keep only if `margin >= 12`
- keep only if `max_correlation <= 0.65`
3. Turnover preference: `8%` to `15%`.
4. Any run above correlation cap is auto `watch/reject` regardless of fitness.

## Result Table (Fill After Simulations)

| Candidate | Fitness | Margin | Sharpe | Turnover | Max DD | Self Corr | Max Corr | Min Corr | Gate Pass | Notes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| P1 |  |  |  |  |  |  |  |  |  |  |
| P2 |  |  |  |  |  |  |  |  |  |  |
| H1 |  |  |  |  |  |  |  |  |  |  |
| H2 |  |  |  |  |  |  |  |  |  |  |

## Controlled Mutation (Only if None Pass)

- Choose top candidate by `fitness` among failing set.
- Run exactly one mutation:
  - increase strongest leg `+0.10`
  - decrease noisiest leg `-0.10`
- Keep the same settings and family policy.
- Stop cycle after this one mutation and log outcome.

## Next-Cycle Recommendation (Fill)

- Winner candidate: `<id>`
- Deployment status: `keep / watch / reject`
- Reason:
- Suggested v3 direction:
