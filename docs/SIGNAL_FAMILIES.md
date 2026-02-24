# Signal Families Map

This map groups all current hypotheses into reusable signal families so future iteration is easier and more systematic.

## 1) Price / Microstructure Reversion

Economic idea:
- Temporary close-location dislocations (vs VWAP or short-term price path) partially mean revert.

Hypotheses:
- `11c032ff5d36` Short-term reversal
- `1053e8f4bb48` VWAP reversion test
- `d7ae4fcfc377` VWAP test package

Typical expression motifs:
- `rank(vwap/close)`
- `rank(vwap - close)`
- `-rank(ts_delta(close, 1))`
- `rank((high + low)/2 - close)`

## 2) News / Attention / Sentiment Flow

Economic idea:
- News-flow shocks can create either continuation (information) or overshoot (attention/execution), depending on regime.

Hypotheses:
- `dd7f42aeaef2` Submitted - News/Reversion Hard Gate
- `44aa91fb46c1` Submitted - Hot News with Price Reversion
- `1ef2926b0aff` Next - Smoothed News + VWAP Reversion

Typical expression motifs:
- `vec_avg(nws12_afterhsz_sl)`
- `rank(ts_sum(..., 60/90/120))`
- gated or blended combination with reversion terms

## 3) Options Skew / Risk-Aversion Repricing

Economic idea:
- Put-call IV skew reflects tail-risk demand and crowding; relative extremes may normalize cross-sectionally.

Hypotheses:
- `14f02817e70e` Submitted - VRP + Skew Hybrid
- `3a0ff732e0aa` Submitted - Options Skew by Size Bucket
- `c0923931898f` Next - Normalized Skew by Size Bucket

Typical expression motifs:
- `(iv_put + iv_call)/2 - historical_volatility_120` (VRP)
- `(put_iv - call_iv) / (put_iv + call_iv)`
- `ts_mean(...)`, `ts_zscore(...)`
- `group_rank(...)` in size buckets via `densify(bucket(rank(cap), ...))`

## 4) Fundamental Value Mispricing

Economic idea:
- Valuation spreads revert over medium/long horizon; timing legs can improve entry points.

Hypotheses:
- `516d0c7349a5` Submitted - EV/EBITDA + Reversion Mix
- `72f865aeb516` Next - Value + VWAP Timing

Typical expression motifs:
- `-rank(ts_zscore(enterprise_value / ebitda, ...))`
- mixed with short-horizon timing components

## 5) Liquidity Dislocation

Economic idea:
- Liquidity stress and temporary participation imbalance can create short-lived mispricing.

Hypotheses:
- `ecc36aaa1bb8` Custom liquidity edge

Typical expression motifs:
- custom liquidity factor ranking (for example `rank(custom_liq_score)`)

## 6) Hybrid / Composite Signals

Economic idea:
- Combine orthogonal mechanisms (information flow, valuation, microstructure) for smoother, more robust alpha.

Hypotheses:
- `dd7f42aeaef2` (news + reversion, hard gate)
- `44aa91fb46c1` (news + short-term reversion, gate)
- `1ef2926b0aff` (news + reversion, smooth blend)
- `516d0c7349a5` (value + reversion)
- `72f865aeb516` (value + VWAP timing)
- `14f02817e70e` (VRP + skew blend, smoothed)

## Practical Use

When proposing next ideas:
1. Pick one base family.
2. Decide if you are doing pure-family or hybrid-family iteration.
3. Keep settings profile fixed (`usa_top3000_consistency_v1`) for fair comparison.
4. Log result plus causal notes (`why_worked`, `why_failed`, `economic_intuition`) after each run.
