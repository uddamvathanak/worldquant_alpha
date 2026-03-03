# Alpaca Paper Trading (Public-Safe)

This folder runs the local daily paper-trading pipeline with:
- signal generation from Alpaca daily bars (price/volume model)
- signal CSV ingestion (`symbol,score,sector`)
- sector-neutral long/short target construction
- Alpaca execution
- SQLite + CSV telemetry
- monthly proxy metrics (`fitness`, `sharpe`, `margin`)

## Public-safe policy

Safe to commit:
- code and docs under `paper/alpaca/`
- templates like `paper/alpaca/.env.example`

Never commit:
- `paper/alpaca/.env`
- `paper/alpaca/logs/*`
- `paper/alpaca/state/*`
- `paper/alpaca/private/*`
- `paper/alpaca/signals/*.csv`

## Folder layout

- `paper/alpaca/smoke_test.py` connectivity check
- `paper/alpaca/universe_builder.py` auto-build liquid tradable universe CSV
- `paper/alpaca/signal_generator.py` generate daily signal CSV from Alpaca market data
- `paper/alpaca/daily_pipeline.py` refresh universe, generate signal, then rebalance
- `paper/alpaca/rebalance_runner.py` daily entrypoint
- `paper/alpaca/liquidate_all.py` manual flatten-all command
- `paper/alpaca/monthly_eval.py` month-end evaluator
- `paper/alpaca/install_scheduler.ps1` Windows task installer
- `paper/alpaca/signals/YYYY-MM-DD.csv` daily signal drop
- `paper/alpaca/state/paper_trading.db` runtime database
- `paper/alpaca/logs/*.csv` daily and monthly exports

## Signal file contract

Required columns:
- `symbol`
- `score`
- `sector`

Optional column:
- `asof_date` (must match trade date when present)

Path convention:
- `paper/alpaca/signals/YYYY-MM-DD.csv` (ET date)

## Run commands

Connectivity:
```powershell
conda run -n alpaca-paper python paper/alpaca/smoke_test.py
```

Generate daily signal file only:
```powershell
conda run -n alpaca-paper python paper/alpaca/signal_generator.py
```

Build/refresh universe file only (TOP3000-style liquid proxy):
```powershell
conda run -n alpaca-paper python paper/alpaca/universe_builder.py --max-symbols 3000 --shortable-only
```

Run full daily pipeline (generate signal + rebalance):
```powershell
conda run -n alpaca-paper python paper/alpaca/daily_pipeline.py
```

Daily rebalance:
```powershell
conda run -n alpaca-paper python paper/alpaca/rebalance_runner.py
```

Dry run (no order submission):
```powershell
conda run -n alpaca-paper python paper/alpaca/rebalance_runner.py --dry-run
```

Run specific trade date:
```powershell
conda run -n alpaca-paper python paper/alpaca/rebalance_runner.py --date 2026-02-25
```

Manual flatten (preview only):
```powershell
conda run -n alpaca-paper python paper/alpaca/liquidate_all.py --dry-run
```

Manual flatten (live close-all):
```powershell
conda run -n alpaca-paper python paper/alpaca/liquidate_all.py --yes
```

Monthly evaluation:
```powershell
conda run -n alpaca-paper python paper/alpaca/monthly_eval.py --month 2026-02
```

## Scheduler setup (Windows)

Install scheduled task:
```powershell
powershell -ExecutionPolicy Bypass -File paper/alpaca/install_scheduler.ps1
```

Install with ET market-time tracking (recommended when your PC timezone is not ET):
```powershell
powershell -ExecutionPolicy Bypass -File paper/alpaca/install_scheduler.ps1
```

Check task status:
```powershell
schtasks /Query /TN "WQA_Alpaca_Rebalance_0935ET" /V /FO LIST
```

Default behavior:
- Task name: `WQA_Alpaca_Rebalance_0935ET`
- Weekdays at `09:35` local system time (set your system clock to ET if needed)
- Wake-to-run enabled
- Network required
- Retry 2x every 10 minutes
- 20-minute execution cap
- ET tracking creates one or two local trigger times depending on DST conversion.
- Daily pipeline enforces ET time window (`09:35 ET +/- 20m`) to avoid double execution when two local triggers exist.
- If task registration returns `Access is denied`, run PowerShell as Administrator and re-run installer.

## Runtime behavior

- Long/short selection: top/bottom N (default 30 each)
- Gross exposure: 80% total (40% long, 40% short)
- Neutralization: dollar-neutral + sector-matched book
- Strict target match: symbols held but missing from today target are added as synthetic `flat` targets and closed.
- Short execution: whole-share `qty` market orders (avoids Alpaca fractional short restriction)
- Short reject policy: drop rejected shorts and run one corrective pass that re-neutralizes both sides.
- Kill switch: skip new entries if prior daily return <= -2%
- Skip-day semantics: skip statuses hold positions; no forced flatten.
- Cost model: 5 bps round-trip for proxy metric accounting
- Missed runs: logged, never backfilled late

## Data coverage (important)

The automated signal generator currently uses Alpaca market data fields:
- `open`, `high`, `low`, `close`, `vwap`, `volume`

It does not natively compute WorldQuant-style non-price fields (for example news sentiment, analyst revisions, options IV, EV/EBITDA) unless you add external providers.

## Universe and sector files

Universe is now auto-refreshable from Alpaca assets + bar liquidity ranking:
- default output path: `paper/alpaca/private/universe.csv`
- default mode: `shortable_only`
- default ranking window: 20 days
- default min filters: average close >= 3, coverage >= 80%, average dollar volume >= 0
- default cap: top 3000 by average dollar volume

Daily pipeline refreshes the universe before signal generation unless you pass `--skip-universe-refresh`.

To generate signals manually without refresh, add:
- `paper/alpaca/private/universe.csv` with column `symbol` (extra columns allowed)
- optional `paper/alpaca/private/sector_map.csv` with columns `symbol,sector`

If sector map is missing, generator uses `sector=ALL`.

Starter templates:
- `paper/alpaca/universe.example.csv`
- `paper/alpaca/sector_map.example.csv`
