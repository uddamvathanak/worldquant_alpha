# Factor Exposure Cycle v5 (External Resource-Driven)

Cycle v5 translates external resource motifs into compile-aware submit candidates with strict gates.

## Locked Settings

### v5_usa_d1_locked

- Instrument/Region/Universe: `Equity / USA / TOP3000`
- Delay/Decay: `1 / 20`
- Neutralization: `Subindustry`
- Truncation: `0.05`
- Pasteurization: `On`
- NaN Handling: `Off`
- Unit Handling: `Verify`

### v5_usa_d0_locked

- Instrument/Region/Universe: `Equity / USA / TOP3000`
- Delay/Decay: `0 / 8`
- Neutralization: `Subindustry`
- Truncation: `0.05`
- Pasteurization: `On`
- NaN Handling: `Off`
- Unit Handling: `Verify`

## Candidate Registry

| Candidate | Hypothesis ID | Branch Tag | Profile | Brief |
|---|---|---|---|---|
| D1-1 | `8f6c2b1a9d40` | `usa_d1` | `v5_usa_d1_locked` | `reports/V5_D1_1_simulation_brief.md` |
| D1-2 | `3c5e7a2f914b` | `usa_d1` | `v5_usa_d1_locked` | `reports/V5_D1_2_simulation_brief.md` |
| D1-3 | `a1d4c8e9b2f6` | `usa_d1` | `v5_usa_d1_locked` | `reports/V5_D1_3_simulation_brief.md` |
| D1-4 | `f0b7d2c4a9e1` | `usa_d1` | `v5_usa_d1_locked` | `reports/V5_D1_4_simulation_brief.md` |
| D0-1 | `5e9a1c3d7b4f` | `usa_d0` | `v5_usa_d0_locked` | `reports/V5_D0_1_simulation_brief.md` |
| D0-2 | `2b8d6f1a4c93` | `usa_d0` | `v5_usa_d0_locked` | `reports/V5_D0_2_simulation_brief.md` |
| D0-3 | `c7a2e5d9f1b3` | `usa_d0` | `v5_usa_d0_locked` | `reports/V5_D0_3_simulation_brief.md` |
| D0-4 | `9d3b1f6a8c24` | `usa_d0` | `v5_usa_d0_locked` | `reports/V5_D0_4_simulation_brief.md` |

## Compile Fallback Rules

- If `book_value_per_share` fails in `D1-3`, replace only value leg:

```text
val = -rank(ts_zscore(enterprise_value / ebitda, 126));
```

- If `short_interest` fails in `D0-3`, replace only crowding leg:

```text
crowd = rank(ts_mean((implied_volatility_put_180 - implied_volatility_call_180) / (implied_volatility_put_180 + implied_volatility_call_180), 5));
```

- `D0-4` exploratory leg `eps_ttm` is unavailable in this account, so use this as primary earnings leg:

```text
earnp = rank(ts_delta(anl4_afv4_eps_high, 20));
```

## Execution Workflow

1. Compile-check all 8 candidates.
2. Apply fallback only to failing leg.
3. Re-compile repaired candidates.
4. Simulate all compile-clean candidates with locked branch profile.
5. Log full metrics plus qualitative notes plus provenance.
6. Rank by `fitness`, then `margin`, then `sharpe`, then lower `max_correlation`.
7. Keep only candidates passing hard gates.

## Hard Gates

- `fitness >= 1.5`
- `margin >= 12`
- `max_correlation <= 0.65`

## Copy-Ready Log Commands

```bash
wqa log-result --hypothesis-id 8f6c2b1a9d40 --simulation-id "<brain-sim-id>" --settings-profile v5_usa_d1_locked --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=external_resources/alpha_ideas.md;motif=price_volume_corr_plus_vwap;fallback_used=none;profile_id=v5_usa_d1_locked"

wqa log-result --hypothesis-id 3c5e7a2f914b --simulation-id "<brain-sim-id>" --settings-profile v5_usa_d1_locked --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=external_resources/alpha_ideas.md;motif=close_location_volume_plus_range;fallback_used=none;profile_id=v5_usa_d1_locked"

wqa log-result --hypothesis-id a1d4c8e9b2f6 --simulation-id "<brain-sim-id>" --settings-profile v5_usa_d1_locked --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=external_resources/submitted_alphas.md;motif=value_revision_news;fallback_used=val=-rank(ts_zscore(enterprise_value / ebitda, 126));profile_id=v5_usa_d1_locked"

wqa log-result --hypothesis-id f0b7d2c4a9e1 --simulation-id "<brain-sim-id>" --settings-profile v5_usa_d1_locked --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=external_resources/alpha_ideas.md;motif=vrp_skew_size_bucket;fallback_used=none;profile_id=v5_usa_d1_locked"

wqa log-result --hypothesis-id 5e9a1c3d7b4f --simulation-id "<brain-sim-id>" --settings-profile v5_usa_d0_locked --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=external_resources/alpha_ideas.md;motif=failed_move_vwap;fallback_used=none;profile_id=v5_usa_d0_locked"

wqa log-result --hypothesis-id 2b8d6f1a4c93 --simulation-id "<brain-sim-id>" --settings-profile v5_usa_d0_locked --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=external_resources/alpha_ideas.md;motif=intraday_pressure_volume_impulse;fallback_used=none;profile_id=v5_usa_d0_locked"

wqa log-result --hypothesis-id c7a2e5d9f1b3 --simulation-id "<brain-sim-id>" --settings-profile v5_usa_d0_locked --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=external_resources/submitted_alphas.md;motif=short_interest_crowding_plus_overextension;fallback_used=crowd=rank(ts_mean((implied_volatility_put_180 - implied_volatility_call_180) / (implied_volatility_put_180 + implied_volatility_call_180), 5));profile_id=v5_usa_d0_locked"

wqa log-result --hypothesis-id 9d3b1f6a8c24 --simulation-id "<brain-sim-id>" --settings-profile v5_usa_d0_locked --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --metric max_correlation=<max_corr> --metric min_correlation=<min_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "source_ref=external_resources/submitted_alphas.md;motif=earnings_price_rank_plus_flow;fallback_used=applied_primary_earnp_revision;profile_id=v5_usa_d0_locked"
```

## Result Table

| Candidate | Fitness | Margin | Sharpe | Turnover | Max DD | Self Corr | Max Corr | Min Corr | Gate Pass | Notes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| D1-1 |  |  |  |  |  |  |  |  |  |  |
| D1-2 |  |  |  |  |  |  |  |  |  |  |
| D1-3 |  |  |  |  |  |  |  |  |  |  |
| D1-4 |  |  |  |  |  |  |  |  |  |  |
| D0-1 |  |  |  |  |  |  |  |  |  |  |
| D0-2 |  |  |  |  |  |  |  |  |  |  |
| D0-3 |  |  |  |  |  |  |  |  |  |  |
| D0-4 |  |  |  |  |  |  |  |  |  |  |

## Mutation Rule (only if none pass)

- Select highest-fitness candidate from this batch.
- Run exactly one mutation:
- `+0.10` strongest leg
- `-0.10` noisiest leg
- Keep same branch profile and hard gates.
- End cycle after this one mutation.
