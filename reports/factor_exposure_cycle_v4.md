# Factor Exposure Cycle v4 (Recovery Pack)

Cycle v4 is a recovery pack after v3 broad failure.

## Locked Settings

- Profile: `usa_top3000_consistency_d20_v1`
- Instrument/Region/Universe: `Equity / USA / TOP3000`
- Delay/Decay: `1 / 20`
- Neutralization: `Subindustry`
- Truncation: `0.05`
- Pasteurization: `On`
- NaN Handling: `Off`
- Unit Handling: `Verify`

## Recovery Policy

- Re-anchor on motifs that were previously strongest in your run history.
- Keep formulas simpler (fewer fragile interactions).
- Preserve deterministic keep rules:
  - `fitness >= 1.5`
  - `margin >= 12`
  - `max_correlation <= 0.65`

## Candidate Registry

| Candidate | Hypothesis ID | Family | Brief |
|---|---|---|---|
| V4-S1 | `a121cb79a88a` | hybrid_family | `reports/V4_S1_simulation_brief.md` |
| V4-S2 | `56e7155d7ef2` | hybrid_family | `reports/V4_S2_simulation_brief.md` |
| V4-S3 | `5372b1a5ee50` | hybrid_family | `reports/V4_S3_simulation_brief.md` |
| V4-S4 | `c5302e4fe510` | pure_family | `reports/V4_S4_simulation_brief.md` |

## Compile Fallback

If `anl4_afv4_eps_high` fails (S2/S3), replace only the revision leg:

```text
rev = rank(ts_zscore(ts_backfill(anl4_fcf_flag, 120), 63));
```

## Copy-Ready Log Commands

```bash
wqa log-result --hypothesis-id a121cb79a88a --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=local;motif=accepted_flow_recovery;fallback_used=none"

wqa log-result --hypothesis-id 56e7155d7ef2 --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=local;motif=proven_vrp_skew_recovery;fallback_used=none"

wqa log-result --hypothesis-id 5372b1a5ee50 --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=local;motif=value_revision_news_recovery;fallback_used=none"

wqa log-result --hypothesis-id c5302e4fe510 --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=local;motif=size_pressure_liquidity_news;fallback_used=none"
```

## Result Table

| Candidate | Fitness | Margin | Sharpe | Turnover | Max DD | Max Corr | Gate Pass | Notes |
|---|---:|---:|---:|---:|---:|---:|---|---|
| V4-S1 |  |  |  |  |  |  |  |  |
| V4-S2 |  |  |  |  |  |  |  |  |
| V4-S3 |  |  |  |  |  |  |  |  |
| V4-S4 |  |  |  |  |  |  |  |  |

## Mutation Rule (only if none pass)

- Pick best-by-fitness candidate.
- Run one mutation only:
  - `+0.10` strongest leg
  - `-0.10` weakest/noisiest leg
- Keep same settings and family policy.
