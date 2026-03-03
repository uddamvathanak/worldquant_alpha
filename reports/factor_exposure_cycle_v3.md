# Factor Exposure Cycle v3 (Non-Options-Core Pivot)

This cycle is a deliberate pivot after v2 full rejection.

## Locked Settings

- Profile: `usa_top3000_consistency_d20_v1`
- Instrument/Region/Universe: `Equity / USA / TOP3000`
- Delay/Decay: `1 / 20`
- Neutralization: `Subindustry`
- Truncation: `0.05`
- Pasteurization: `On`
- NaN Handling: `Off`
- Unit Handling: `Verify`

## Pivot Policy

- Reduce overlap with crowded options/flow cluster.
- Use mostly non-options exposures.
- Keep deterministic pass rules:
  - `fitness >= 1.5`
  - `margin >= 12`
  - `max_correlation <= 0.65`

## Candidate Registry

| Candidate | Hypothesis ID | Family | Brief |
|---|---|---|---|
| V3-P1 | `67fc160cadc5` | pure_family | `reports/V3_P1_simulation_brief.md` |
| V3-P2 | `01a1bfba4ed0` | pure_family | `reports/V3_P2_simulation_brief.md` |
| V3-H1 | `a67237f4fe32` | hybrid_family | `reports/V3_H1_simulation_brief.md` |
| V3-H2 | `e962e174ec13` | hybrid_family | `reports/V3_H2_simulation_brief.md` |

## Compile Fallback

If `anl4_afv4_eps_high` fails, replace only that leg:

```text
rev = rank(ts_zscore(ts_backfill(anl4_fcf_flag, 120), 63));
```

Log fallback in notes: `fallback_used=rev->anl4_fcf_flag`.

## Copy-Ready Log Commands

```bash
wqa log-result --hypothesis-id 67fc160cadc5 --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=local;motif=range_liquidity;fallback_used=none"

wqa log-result --hypothesis-id 01a1bfba4ed0 --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=local;motif=value_news_divergence;fallback_used=none"

wqa log-result --hypothesis-id a67237f4fe32 --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=local;motif=revision_size_pressure;fallback_used=none"

wqa log-result --hypothesis-id e962e174ec13 --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=local;motif=news_value_liquidity;fallback_used=none"
```

## Result Table (Fill after BRAIN runs)

| Candidate | Fitness | Margin | Sharpe | Turnover | Max DD | Max Corr | Gate Pass | Notes |
|---|---:|---:|---:|---:|---:|---:|---|---|
| V3-P1 |  |  |  |  |  |  |  |  |
| V3-P2 |  |  |  |  |  |  |  |  |
| V3-H1 |  |  |  |  |  |  |  |  |
| V3-H2 |  |  |  |  |  |  |  |  |

## Next Decision Rule

- If at least one candidate passes all hard rules, pick highest fitness among passers.
- If none pass, run exactly one mutation on best-by-fitness candidate:
  - `+0.10` strongest leg
  - `-0.10` weakest/noisiest leg
  - keep same settings and no ad hoc expression redesign.
