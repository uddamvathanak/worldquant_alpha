# Alpaca Strategy Research Shortlist

This file is the research starting set for the Alpaca paper engine.

The goal is not to copy academic papers literally. The goal is to translate the parts that fit our data and execution stack:
- Alpaca `sip` daily bars
- cached SEC-derived sector/industry classifications
- `TOP3000` liquid U.S. universe
- 4.0 total gross exposure
- sector-neutral weighted book construction
- next-day-open replay in the backtest engine

## Frozen baseline

All new research candidates should be compared on the same static baseline:
- baseline file: `paper/alpaca/research_baseline.json`
- baseline id: `sip_top3000_weighted_2026q1_v1`
- end date: `2026-03-20`
- classification snapshot: `2026-03-17`
- split: `756 / 252 / 252`
- feed: `sip`
- book mode: `sector_weighted`
- top_n metadata: `3000`

The split is shorter than the ideal `1008 / 252 / 252` WorldQuant-style layout because the free SIP history in the local environment does not yet support a deeper stable window. Freezing the shorter window is better than letting the sample drift.

## Literature-backed first wave

### 1. Skip-month momentum
- Template: `skip_month_momentum`
- Family: `literature_momentum`
- Why it is here:
  - medium-horizon momentum remains one of the most durable cross-sectional effects
  - skipping the most recent month avoids contaminating the signal with short-term reversal
- Parameters to test first:
  - `lookback = 126, 189, 252`
  - `skip = 21`

### 2. 52-week-high proximity
- Template: `high_52w_proximity`
- Family: `literature_momentum`
- Why it is here:
  - nearness to the trailing high is a distinct predictor from plain trailing-return momentum
  - it is easy to compute from daily bars and fits the current engine cleanly
- Parameters to test first:
  - `window = 126, 189, 252`

### 3. Low-volatility defensive
- Template: `low_volatility_defensive`
- Family: `low_volatility`
- Why it is here:
  - low-volatility long/short sorting is well documented
  - it is implementation-friendly in a daily hedged book
  - it is also a useful stabilizer when momentum is noisy
- Parameters to test first:
  - `window = 42, 63, 126`

### 4. Volume-confirmed momentum
- Template: `momentum_with_volume_confirm`
- Family: `momentum`
- Why it is here:
  - price continuation often behaves differently when participation is strong
  - this gives us a cleaner daily-bar approximation of the price/volume interaction literature than pure short-horizon reversal
- Parameters to test first:
  - existing registry grid
  - priority windows: `20, 42, 63`

### 5. Smooth momentum and breakout quality
- Templates:
  - `smooth_momentum`
  - `breakout_quality`
- Why they stay in the shortlist:
  - they are practical price-only approximations of medium-horizon continuation
  - they tend to be less fragile than 1-day contrarian signals in a weighted neutral book

### 6. VWAP gap reversion as a control
- Template: `vwap_gap_revert`
- Why it stays:
  - it is a useful control family for short-horizon mean reversion
  - we should keep one reversion family in the screen, but it should not dominate the search budget

### 7. Proxy control
- Template: `profit_asset_gate_proxy_v1`
- Why it stays:
  - this is the current baseline we already know is weak
  - keeping it in the search tells us whether new ideas truly beat the incumbent

## What not to over-prioritize right now

- `rev_close_1d`
  - current evidence in our engine is weak and underfills the sector-neutral book too easily
- giant random formula grids
  - they are expensive, hard to interpret, and easier to overfit
- dynamic moving end dates
  - they make the cache less useful and distort model ranking over time

## How to use this shortlist

1. Start with the `literature_core` alpha set.
2. Keep the frozen baseline fixed while screening.
3. Keep `TOP3000` and `sector_weighted` fixed while choosing the best family.
4. Only after a family looks real on the frozen unseen year should we vary:
   - group level
   - decay
   - truncation
   - universe cap
   - basket combinations

## References

- Jegadeesh, N., and Titman, S. (1993), "Returns to Buying Winners and Selling Losers"
- George, T., and Hwang, C. Y. (2004), "The 52-Week High and Momentum Investing"
- Lee, C., and Swaminathan, B. (2000), "Price Momentum and Trading Volume"
- Blitz, D., and van Vliet, P. (2007), low-volatility anomaly research
- AQR, "The Low-Volatility Anomaly: Market Evidence on Systemic Risk vs. Mispricing"
