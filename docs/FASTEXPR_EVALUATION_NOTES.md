# Fast Expression Evaluation Notes (WorldQuant BRAIN)

This file is a memory guide for systematic alpha research.

Important:
- Treat this as a working research model, not an official platform specification.
- Keep the exact formulas and ordering aligned with your own BRAIN `Learn` pages and simulation settings.

## 1) Keep These Settings Fixed When Comparing Alphas

If these change, your comparison is not apples-to-apples:
- `Region`
- `Universe`
- `Delay`
- `Decay`
- `Neutralization`
- `Truncation`
- `Pasteurization`
- `NaN Handling`
- `Unit Handling`
- `Test Period`

## 2) Practical Evaluation Pipeline (Mental Model)

For each date `t`, think in this order:

1. Universe selection
- Build the active instrument universe for date `t`.

2. Raw expression evaluation
- Compute raw alpha value `a_raw(i, t)` for each instrument `i`.
- Example expression: `vwap/close`.

3. Delay alignment
- `Delay=1`: signal built from information up to `t`, applied to next trade day.
- `Delay=0`: same-day signal/trade context (if allowed by configuration).

4. Missing data / units handling
- Apply platform rules for NaN and unit consistency before portfolio construction.

5. Decay (time smoothing)
- Smooth signal over recent days (if decay > 0).
- Common research approximation:
  `a_decay(i,t) = sum_k w_k * a_raw(i,t-k) / sum_k w_k` with recent days weighted more.

6. Neutralization
- Remove group-level exposure (market/sector/industry/etc. depending on setting).
- Common research approximation in each bucket `g`:
  `a_neut(i,t) = a_decay(i,t) - mean_{j in g}(a_decay(j,t))`.

7. Truncation / cap
- Limit extreme values to reduce concentration risk.
- Common approximation:
  `a_cap(i,t) = clip(a_neut(i,t), -cap, +cap)`.

8. Scale to portfolio weights and book size
- Convert capped signal into tradable weights.
- Common approximation:
  `w(i,t) = a_cap(i,t) / sum_j |a_cap(j,t)|`.
- Dollar notional:
  `notional(i,t) = w(i,t) * BookSize`.

9. Realized return and metrics
- Next-day PnL approximation:
  `PnL(t+1) = sum_i w(i,t) * r(i,t+1)`.
- Evaluate with IC, Sharpe, turnover, drawdown, hit rate, etc.

## 3) Example: `vwap/close`

Expression:
- `vwap/close`

Interpretation:
- `vwap/close > 1`: close is below VWAP (weaker close vs intraday average).
- `vwap/close < 1`: close is above VWAP (stronger close vs intraday average).

Hypothesis variants:
- Mean reversion idea:
  `rank(vwap/close)` (buy weaker closes if they rebound next day).
- Momentum idea:
  `-rank(vwap/close)` (buy stronger closes if strength persists).

Use the same simulation settings, test both, then compare IC and risk-adjusted metrics.

## 4) Objective Metrics To Track

Primary objectives to log from platform results:
- `fitness` (primary)
- `margin` (secondary)

Secondary diagnostics:
- `sharpe`
- `ic`
- `turnover`
- `max_drawdown`

## 5) Alpha Journal Template (Use This Every Run)

| Date | Hypothesis | Fast Expression | Settings Snapshot | IC Mean | Sharpe | Turnover | Result | Next Step |
|---|---|---|---|---:|---:|---:|---|---|
| 2026-02-10 | Weak close rebounds | `rank(vwap/close)` | `US TOP3000, D1, Decay 4, Neutralization Subindustry, Trunc 0.08` | 0.012 | 1.15 | 0.42 | Keep | Add volatility filter |

Recommended rule:
- Change only one thing per run (expression OR one setting), then log.

## 6) Anti-Forgetting Checklist

Before each run:
- Confirm settings are unchanged from baseline.
- Confirm expression version is tracked exactly.
- Confirm delay interpretation matches your target horizon.

After each run:
- Log numeric metrics from platform.
- Write why it worked and why it did not.
- Add economic intuition and next experiment step.

## 7) How To Use This Repo With These Notes

1. Add hypothesis metadata:
```bash
wqa add-hypothesis --title "..." --rationale "..." --expression "..."
```

2. Log run results from platform:
```bash
wqa log-result --hypothesis-id <ID> --simulation-id "brain-sim-123" --fitness 1.2 --margin 30 --sharpe 1.8 --status keep --why-worked "..." --economic-intuition "..." --next-step "..."
```

3. Compare runs:
```bash
wqa leaderboard --sort-by fitness --limit 20
```

## 8) Verification To-Do (Fill From Your Account)

Keep your own platform-confirmed details here:
- Exact order of decay vs neutralization vs truncation in BRAIN.
- Exact truncation math in simulation engine.
- Exact weight scaling rules used for your selected settings.

When your account docs clarify one of these, update this file immediately.
