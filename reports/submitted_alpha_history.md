# Submitted Alpha History

Last updated: 2026-03-04

Scope: confirmed entries already marked as submitted/accepted in your log, plus your latest submitted formula.

## Snapshot

- Confirmed submitted entries: `7`
- Accepted entries (confirmed): `1`
- Entries with missing full metrics/settings: `6`

## Registry

| # | Hypothesis ID | Status | Name | Settings | Notes |
|---|---|---|---|---|---|
| 1 | `dd7f42aeaef2` | Submitted | News/Reversion Hard Gate | `USA TOP3000, D1, Decay 25, Trunc 0.05, Industry` | Threshold switch between news and VWAP reversion |
| 2 | `3a0ff732e0aa` | Submitted | Options Skew by Size Bucket | `USA TOP3000, D1, Decay 10, Trunc 0.03, Industry` | Size-bucket skew mean reversion |
| 3 | `516d0c7349a5` | Submitted | EV/EBITDA + Reversion Mix | `USA TOP3000, D1, Decay 10, Trunc 0.08, Subindustry` | Value core + short-term timing |
| 4 | `44aa91fb46c1` | Submitted | Hot News with Price Reversion | `USA TOP3000, D1, Decay 5, Trunc 0.05, Sector` | Binary news gate to reversal |
| 5 | `14f02817e70e` | Submitted (good) | VRP + Skew Hybrid | `settings not fully logged` | You flagged this as a good submitted alpha |
| 6 | `058b3684d75e` | Accepted | Overnight vs Intraday Flow + VWAP Reversion | `settings not fully logged` | Confirmed accepted |
| 7 | `7b4c9e12d6f0` | Submitted (new) | Intraday Pressure + Volume Impulse Rank Combo | `settings pending` | Added from latest message |

## Expressions

### 1) `dd7f42aeaef2` Submitted - News/Reversion Hard Gate

```text
news = rank(ts_sum(vec_avg(nws12_afterhsz_sl), 120));
rev = rank(vwap - close);
signal = news > 0.65 ? news : rev;
```

### 2) `3a0ff732e0aa` Submitted - Options Skew by Size Bucket

```text
g = densify(bucket(rank(cap), buckets=15));
put = implied_volatility_put_180;
call = implied_volatility_call_180;
skew = (put - call) / (put + call);
signal = -group_rank(ts_mean(skew, 5), g);
```

### 3) `516d0c7349a5` Submitted - EV/EBITDA + Reversion Mix

```text
val = -rank(ts_zscore(enterprise_value / ebitda, 63));
rev = rank((high + low) / 2 - close);
signal = val + 0.3 * rev;
```

### 4) `44aa91fb46c1` Submitted - Hot News with Price Reversion

```text
avg_news = vec_avg(nws12_afterhsz_sl);
signal = rank(ts_sum(avg_news, 60)) > 0.5 ? 1 : rank(-ts_delta(close, 2));
```

### 5) `14f02817e70e` Submitted - VRP + Skew Hybrid

```text
ivp = implied_volatility_put_180;
ivc = implied_volatility_call_180;
iv = (ivp + ivc) / 2;
hv = historical_volatility_120;
vrp = iv - hv;
skew = (ivp - ivc) / (ivp + ivc);
raw = rank(ts_rank(vrp, 62)) - rank(skew);
signal = ts_mean(raw, 5);
```

### 6) `058b3684d75e` Submitted + Accepted - Overnight vs Intraday Flow + VWAP Reversion

```text
overnight = (open - ts_delay(close, 1)) / ts_delay(close, 1);
intraday = (close - open) / open;
smart_money = rank(overnight);
dumb_money = rank(intraday);
flow_edge = smart_money - dumb_money;
rev = rank(vwap - close);
combo = flow_edge + rev;
signal = ts_mean(combo, 5);
```

### 7) `7b4c9e12d6f0` Submitted (new) - Intraday Pressure + Volume Impulse Rank Combo

```text
press = ts_mean((2 * close - high - low) / (high - low), 2);
press_r = rank(press);
volimp = ts_mean(volume / ts_mean(volume, 20), 2);
vol_r = rank(volimp);
combo = -0.45 * press_r + 0.55 * vol_r;
signal = rank(combo);
```

## Missing Data to Fill

- Exact simulation settings for entries `14f02817e70e` and `058b3684d75e`
- Full run metrics (Sharpe, Turnover, Fitness, Returns, Drawdown, Margin) for all entries
- Correlation metrics (`self_correlation`, `max_correlation`, `min_correlation`) for later filtering
