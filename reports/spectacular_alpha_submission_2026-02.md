# Submittable Alpha Cycle v1 (2026-02)

> Superseded for current work by Cycle v2 tracker: `reports/factor_exposure_cycle_v2.md`  
> Web provenance notes: `reports/web_factor_sources_2026-02.md`

This cycle is pass-gate-first: submit three candidates with the same knobs, compare directly, and keep only what clears `fitness >= 1.5` and `margin >= 12`.

## Locked Submission Configuration

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
- Max Trade: `OFF`
- Max Position: `OFF`
- Settings profile: `usa_top3000_consistency_d20_v1`

## Safe Field Whitelist

- `open`, `high`, `low`, `close`, `vwap`
- `nws12_afterhsz_sl`
- `anl4_afv4_eps_high` (fallback: `anl4_fcf_flag`)
- `implied_volatility_put_180`, `implied_volatility_call_180`
- `historical_volatility_120`
- `cap` (optional)

## Candidate Registry

1. C1
   - Hypothesis ID: `24fae7efbaac`
   - Brief: `reports/C1_simulation_brief.md`
2. C2
   - Hypothesis ID: `acd5b63a666c`
   - Brief: `reports/C2_simulation_brief.md`
3. C3
   - Hypothesis ID: `be8dc5d2c686`
   - Brief: `reports/C3_simulation_brief.md`

## Candidate Expressions

### C1: Confirm + Skew + Orth Timing

```text
news = rank(ts_sum(vec_avg(nws12_afterhsz_sl), 60));
rev = rank(ts_delta(anl4_afv4_eps_high, 20));
confirm = 0.60 * rev + 0.40 * news;

ivp = implied_volatility_put_180;
ivc = implied_volatility_call_180;
skew = (ivp - ivc) / (ivp + ivc);
contr = -rank(ts_mean(skew, 5));

flow = rank((open - ts_delay(close, 1)) / ts_delay(close, 1) - (close - open) / open);
timing_orth = rank(vwap - close) - 0.60 * flow;

signal = 0.55 * confirm + 0.30 * contr + 0.15 * timing_orth;
```

### C2: VRP Core + Revision Overlay

```text
rev = rank(ts_delta(anl4_afv4_eps_high, 20));

ivp = implied_volatility_put_180;
ivc = implied_volatility_call_180;
iv = (ivp + ivc) / 2;
hv = historical_volatility_120;
vrp = rank(ts_rank(iv - hv, 62));
skew = rank((ivp - ivc) / (ivp + ivc));

timing = rank(vwap - close);

signal = 0.40 * vrp - 0.25 * skew + 0.25 * rev + 0.10 * timing;
```

### C3: Flow + News Regime + Skew Change

```text
overnight = (open - ts_delay(close, 1)) / ts_delay(close, 1);
intraday = (close - open) / open;
flow = rank(overnight - intraday);

news_fast = rank(ts_sum(vec_avg(nws12_afterhsz_sl), 30));
news_slow = rank(ts_sum(vec_avg(nws12_afterhsz_sl), 120));
news_spread = news_fast - news_slow;

ivp = implied_volatility_put_180;
ivc = implied_volatility_call_180;
skew = (ivp - ivc) / (ivp + ivc);
contr = -rank(ts_delta(ts_mean(skew, 5), 10));

signal = 0.45 * flow + 0.35 * news_spread + 0.20 * contr;
```

## Analyst Compile Fallback

If `anl4_afv4_eps_high` fails, replace only the revision leg:

```text
rev = rank(ts_zscore(ts_backfill(anl4_fcf_flag, 120), 63));
```

Use fallback only in C1/C2 and log mutation explicitly in notes.

## Evaluation Protocol

1. Submit C1, C2, C3 with identical locked settings.
2. Record: `fitness`, `margin`, `sharpe`, `turnover`, `drawdown`, `self_correlation`.
3. Rank by `fitness`, then `margin`, then `sharpe`.
4. Keep if `fitness >= 1.5` and `margin >= 12`.
5. Preferred turnover band: `8% to 15%`.
6. If none pass, run one variant only on best-by-fitness:
   - Increase strongest timing/confirmation leg by `+0.10`.
   - Decrease noisiest leg by `-0.10`.
   - Keep `Decay = 20`.
7. If close tie, prefer lower `self_correlation`.

## Logging Contract

- Numeric keys: `fitness`, `margin`, `sharpe`, `turnover`, `max_drawdown`, `self_correlation`
- Qualitative keys: `why_worked`, `why_failed`, `economic_intuition`, `next_step`

```bash
wqa log-result --hypothesis-id <HYPOTHESIS_ID> --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "fallback=none"
```

### Candidate-Specific Logging Commands

```bash
wqa log-result --hypothesis-id 24fae7efbaac --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "candidate=C1;fallback=none"
wqa log-result --hypothesis-id acd5b63a666c --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "candidate=C2;fallback=none"
wqa log-result --hypothesis-id be8dc5d2c686 --simulation-id "<brain-sim-id>" --settings-profile usa_top3000_consistency_d20_v1 --fitness <fitness> --margin <margin> --sharpe <sharpe> --turnover <turnover> --max-drawdown <drawdown> --metric self_correlation=<self_corr> --status candidate --why-worked "<why worked>" --why-failed "<why failed>" --economic-intuition "<economic story>" --next-step "<next test>" --notes "candidate=C3;fallback=none"
```
