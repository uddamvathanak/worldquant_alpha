# Fast Expression Operations Reference (Practical)

Use this as an operator memory sheet. Names and exact behavior can vary by platform version; verify against your in-account Learn pages.

## Cross-Sectional Building Blocks

- `rank(x)`: Converts a raw field into relative cross-sectional strength.
- `zscore(x)`: Standardizes distribution; useful when combining terms.
- `scale(x)`: Rescales exposure magnitude.
- `winsorize(x, limit)` (or truncation setting): Controls outliers.

Example:
```text
rank(vwap/close)
```

## Time-Series Building Blocks

- `ts_delta(x, d)`: Change over `d` periods.
- `ts_mean(x, d)`: Rolling mean.
- `ts_std_dev(x, d)`: Rolling volatility.
- `ts_rank(x, d)`: Relative position in recent history.
- `ts_corr(x, y, d)`: Rolling correlation.
- `delay(x, d)`: Shift in time (if supported in expression context).

Examples:
```text
-rank(ts_delta(close, 5))
rank(ts_delta(close, 10) / ts_std_dev(returns, 20))
```

## Group / Neutralization Style Operations

- Group rank/mean-neutral transforms (if available) reduce unwanted sector/industry bias.
- If group ops are not in expression, apply neutralization via settings.

Pattern:
```text
raw signal -> group/setting neutralization -> truncation -> weight scale
```

## Composition Patterns

1. Mean reversion:
```text
rank(vwap/close)
```

2. Momentum:
```text
-rank(ts_delta(close, 20))
```

3. Volatility-adjusted:
```text
rank(ts_delta(close, 10) / ts_std_dev(returns, 20))
```

4. Liquidity shock:
```text
rank(ts_delta(volume, 1) / adv20)
```

## Operator Usage Rules of Thumb

- Start simple: one idea, one dominant mechanism.
- Keep windows interpretable: 5/10/20/60 are easier to reason about.
- Normalize before combining unrelated terms.
- Prefer robust ratios over raw levels when scale differs across assets.
- Use settings-level neutralization/truncation first; only add expression complexity if needed.
