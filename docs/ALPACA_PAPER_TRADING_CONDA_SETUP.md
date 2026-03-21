# Alpaca Paper Trading with Conda

This is a practical setup path for paper trading using `alpaca-py`.

## 1) Create and activate a conda environment

```powershell
conda create -n alpaca-paper python=3.11 -y
conda activate alpaca-paper
```

Recommended:
- Python 3.10 or 3.11
- keep this env dedicated to paper-trading execution

## 2) Install dependencies

```powershell
pip install --upgrade pip
pip install -e .[dev]
pip install alpaca-py pandas numpy python-dotenv pydantic pyarrow requests
```

Optional utilities:
```powershell
pip install matplotlib rich loguru
```

## 3) Set paper trading credentials

Copy `paper/alpaca/.env.example` to `paper/alpaca/.env` and fill in values:

```dotenv
APCA_API_KEY_ID=your_paper_key
APCA_API_SECRET_KEY=your_paper_secret
APCA_API_BASE_URL=https://paper-api.alpaca.markets
APCA_DATA_FEED=sip
FMP_API_KEY=your_fmp_key
ALPACA_CLASSIFICATION_SOURCE=fmp
ALPACA_SIGNAL_MODEL=profit_asset_gate_proxy
ALPACA_BOOK_MODE=sector
ALPACA_GROSS_EXPOSURE=4.0
ALPACA_BP_UTILIZATION=0.90
ALPACA_MARGIN_BUFFER_NOTIONAL=0
ALPACA_MAX_RETRY_PASSES=3
ALPHA_SEARCH_BATCH_SIZE=10
ALPHA_SEARCH_MAX_RUNTIME_MIN=480
ALPHA_SEARCH_TASK_NAME=WQA_Alpaca_Research_2300
```

Notes:
- Use **paper** keys, not live keys.
- `FMP_API_KEY` is required the first time you bootstrap cached classifications from FMP.
- `APCA_DATA_FEED=sip` is recommended for historical bars and backtests. Latest snapshot pricing used during rebalancing may still fall back to Alpaca's best available feed.
- Never commit `paper/alpaca/.env` to git.

## 4) Bootstrap cached classifications

Run the free FMP sync once before the proxy model or the backtester:

```powershell
conda run -n alpaca-paper python paper/alpaca/classification_sync.py --snapshot-date 2026-03-17
```

This writes:
- `paper/alpaca/private/reference/classifications_latest.csv`
- `paper/alpaca/private/reference/classifications/YYYY-MM-DD.csv`
- `paper/alpaca/private/reference/symbol_master.csv`

## 5) Minimal connectivity smoke test

Run the built-in smoke test:

```powershell
python paper/alpaca/smoke_test.py
```

Expected output includes account metadata (account number, status, buying power, equity).

## 6) Public repo safety model

Do:
- commit public-safe code and docs under `paper/alpaca/`
- commit templates like `paper/alpaca/.env.example`
- keep operational notes generic and non-sensitive

Do not:
- commit `paper/alpaca/.env`
- commit private strategy details under `paper/alpaca/private/`
- commit runtime logs/state/database/signal files

## 7) Universe + signal pipeline options

You now have three modes:

1. Auto-refresh a liquid universe from Alpaca assets:
```powershell
conda run -n alpaca-paper python paper/alpaca/universe_builder.py --max-symbols 3000 --shortable-only
```

2. Auto-generate signals from Alpaca market data:
```powershell
conda run -n alpaca-paper python paper/alpaca/signal_generator.py
```

3. Provide your own signal CSV manually:
- `paper/alpaca/signals/YYYY-MM-DD.csv` (ET date)
- required columns: `symbol,score,sector`
- optional: `asof_date`

Auto-universe defaults:
- output: `paper/alpaca/private/universe.csv`
- ranking: average dollar volume over 20 days
- min filters: price >= 3, avg dollar volume >= 0, coverage >= 80%
- shortability policy: `shortable_only` by default

Auto-generator expects:
- `paper/alpaca/private/universe.csv` with a `symbol` column
- optional `paper/alpaca/private/sector_map.csv` with columns `symbol,sector`
- cached classifications from `paper/alpaca/private/reference/classifications_latest.csv`
- cached symbol master from `paper/alpaca/private/reference/symbol_master.csv`

## 8) Daily run command

Recommended full pipeline (refresh universe + generate signal + rebalance):
```powershell
conda run -n alpaca-paper python paper/alpaca/daily_pipeline.py
```
With the recommended `.env`, this defaults to `profit_asset_gate_proxy` with sector-matched construction and 4.0 total gross exposure.

If you want to use a fixed universe file and skip refresh:
```powershell
conda run -n alpaca-paper python paper/alpaca/daily_pipeline.py --skip-universe-refresh
```

Dry-run mode (no order submission):
```powershell
conda run -n alpaca-paper python paper/alpaca/daily_pipeline.py --dry-run
```

Rebalance behavior notes:
- strict target-match: dropped symbols are flattened via synthetic `flat` targets.
- reject handling: runner retries rejected orders up to `ALPACA_MAX_RETRY_PASSES` total passes.
- reject correction: if shorts are rejected, runner drops those symbols and re-neutralizes both sides before retry.
- hard margin guard: runner caps incremental risk-open notional using buying power before submission.
- skip statuses hold positions (no auto-flatten).

Run a specific date:
```powershell
conda run -n alpaca-paper python paper/alpaca/daily_pipeline.py --date 2026-02-25
```

## 9) Frozen research baseline

Research now defaults to the committed frozen baseline:
- file: `paper/alpaca/research_baseline.json`
- baseline id: `sip_top3000_weighted_2026q1_v1`
- split: `756 / 252 / 252`
- end date: `2026-03-20`
- classification snapshot: `2026-03-17`

This keeps the candidate cache and leaderboard comparable across nights instead of drifting with the latest available data.

## 10) Backtest runner

Run the staged research sweep on the frozen baseline:

```powershell
conda run -n alpaca-paper python paper/alpaca/research_runner.py
```

Override the static baseline only when you intentionally want a different sample:

```powershell
conda run -n alpaca-paper python paper/alpaca/research_runner.py --dynamic-baseline --end-date 2026-03-20
```

Run the historical proxy backtest directly:

```powershell
conda run -n alpaca-paper python paper/alpaca/backtest_runner.py --end-date 2026-03-16 --feed iex --train-days 1008 --oos-days 252 --test-days 252
```

Outputs are written under:
- `paper/alpaca/private/backtests/<run_stamp>/`

## 11) Nightly search runner

Start a new resumable nightly search run:

```powershell
conda run -n alpaca-paper python paper/alpaca/search_runner.py --new-run
```

Resume the latest search batch:

```powershell
conda run -n alpaca-paper python paper/alpaca/search_runner.py --resume
```

Inspect the latest search status:

```powershell
conda run -n alpaca-paper python paper/alpaca/search_runner.py --status
```

Search outputs are written under:
- `paper/alpaca/private/search_runs/<run_id>/`
- best shadow artifact: `paper/alpaca/private/shadow_strategy.json`
- cross-run candidate cache: `paper/alpaca/state/research_cache.db`

The nightly search is shadow-only and does not overwrite `paper/alpaca/private/selected_strategy.json`.

## 12) Install Windows task scheduler job

```powershell
powershell -ExecutionPolicy Bypass -File paper/alpaca/install_scheduler.ps1
```

If your machine timezone is not ET (for example Singapore), install with ET market-time tracking:

```powershell
powershell -ExecutionPolicy Bypass -File paper/alpaca/install_scheduler.ps1
```

Verify task:

```powershell
schtasks /Query /TN "WQA_Alpaca_Rebalance_0935ET" /V /FO LIST
```

Task defaults:
- name: `WQA_Alpaca_Rebalance_0935ET`
- schedule: weekdays 09:35 (local system timezone)
- wake-to-run: on
- network required: on
- retries: 2 (10-minute interval)
- time limit: 20 minutes
- ET tracking mode derives local trigger times from `09:35 ET` and handles DST shifts.
- Runner ET gate (`--enforce-et-window`) executes only near target ET time to avoid duplicate runs from dual local triggers.
- Scheduler action runs `paper/alpaca/daily_pipeline.py` so universe refresh + signal generation happen before rebalance.

If registration fails with `Access is denied`, open PowerShell as Administrator and rerun the installer.

Install the separate nightly research task:

```powershell
powershell -ExecutionPolicy Bypass -File paper/alpaca/install_research_scheduler.ps1
```

## 13) Month-end proxy evaluation

```powershell
conda run -n alpaca-paper python paper/alpaca/monthly_eval.py --month 2026-02
```

This writes:
- `paper/alpaca/logs/summary_YYYY-MM.csv`
- copy-ready `wqa log-result` command printed in terminal

## 14) Manual emergency flatten command

Preview close-all without placing orders:

```powershell
conda run -n alpaca-paper python paper/alpaca/liquidate_all.py --dry-run
```

Execute close-all now:

```powershell
conda run -n alpaca-paper python paper/alpaca/liquidate_all.py --yes
```

Useful option:
- add `--no-cancel-open-orders` to keep existing open orders untouched

## 14) Suggested project layout for paper trading phase

- `paper/alpaca/classification_sync.py`
- `paper/alpaca/backtest_runner.py`
- `paper/alpaca/search_runner.py`
- `paper/alpaca/smoke_test.py`
- `paper/alpaca/universe_builder.py`
- `paper/alpaca/signal_generator.py`
- `paper/alpaca/daily_pipeline.py`
- `paper/alpaca/rebalance_runner.py`
- `paper/alpaca/liquidate_all.py`
- `paper/alpaca/monthly_eval.py`
- `paper/alpaca/install_scheduler.ps1`
- `paper/alpaca/install_research_scheduler.ps1`
- `paper/alpaca/.env.example`
- `paper/alpaca/signals/`
- `paper/alpaca/logs/`
- `paper/alpaca/state/`
- `paper/alpaca/tmp/`
- `paper/alpaca/private/`

## 15) Operational rules before running continuously

- Add a hard kill switch by max daily drawdown.
- Add notional and symbol concentration limits.
- Keep `ALPACA_MAX_RETRY_PASSES` finite and review unresolved rejects in run logs.
- Record every order and fill locally for audit.
- Run a scheduled heartbeat check for connectivity.
- Keep short orders in whole-share qty mode (fractional short sell is rejected by Alpaca).
- Ensure strict target matching is enabled in runner logic so dropped symbols are actively flattened.
- On skip statuses (`skipped_*`), keep current holdings unchanged unless you manually run `liquidate_all.py`.

