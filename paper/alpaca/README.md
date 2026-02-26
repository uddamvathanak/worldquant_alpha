# Alpaca Paper Trading (Public-Safe)

This folder runs the local daily paper-trading pipeline with:
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
- `paper/alpaca/rebalance_runner.py` daily entrypoint
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

Monthly evaluation:
```powershell
conda run -n alpaca-paper python paper/alpaca/monthly_eval.py --month 2026-02
```

## Scheduler setup (Windows)

Install scheduled task:
```powershell
powershell -ExecutionPolicy Bypass -File paper/alpaca/install_scheduler.ps1
```

Default behavior:
- Task name: `WQA_Alpaca_Rebalance_0935ET`
- Weekdays at `09:35` local system time (set your system clock to ET if needed)
- Wake-to-run enabled
- Network required
- Retry 2x every 10 minutes
- 20-minute execution cap

## Runtime behavior

- Long/short selection: top/bottom N (default 30 each)
- Gross exposure: 80% total (40% long, 40% short)
- Neutralization: dollar-neutral + sector-matched book
- Short reject policy: drop rejected short and run one corrective pass
- Kill switch: skip new entries if prior daily return <= -2%
- Cost model: 5 bps round-trip for proxy metric accounting
- Missed runs: logged, never backfilled late
