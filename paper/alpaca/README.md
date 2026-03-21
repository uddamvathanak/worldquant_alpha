# Alpaca Paper Trading (Public-Safe)

This folder runs the local daily paper-trading pipeline with:
- signal generation from Alpaca daily bars plus cached classification/reference data
- signal CSV ingestion (`symbol,score,sector`)
- configurable long/short target construction (`sector` or `none`)
- Alpaca execution
- SQLite + CSV telemetry
- monthly proxy metrics (`fitness`, `sharpe`, `margin`)
- a live-like historical backtest runner for the proxy strategy

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
- `paper/alpaca/classification_sync.py` fetch and cache free FMP classifications
- `paper/alpaca/backtest_runner.py` run the staged research/backtest workflow
- `paper/alpaca/research_runner.py` stage family sweeps, filters, basket selection, and promotion
- `paper/alpaca/search_runner.py` resumable nightly single-alpha search with shadow-only output
- `paper/alpaca/alpha_registry.py` declare research alpha families and selected-strategy schema
- `paper/alpaca/alpha_templates.py` compute price/volume alpha panels and score post-processing
- `paper/alpaca/alpha_dsl.py` validate strict JSON mutation candidates against the registry
- `paper/alpaca/free_model_generator.py` optional OpenAI-compatible mutation adapter
- `paper/alpaca/universe_builder.py` auto-build liquid tradable universe CSV
- `paper/alpaca/signal_generator.py` generate daily signal CSV from Alpaca market data
- `paper/alpaca/daily_pipeline.py` refresh universe, generate signal, then rebalance
- `paper/alpaca/rebalance_runner.py` daily entrypoint
- `paper/alpaca/liquidate_all.py` manual flatten-all command
- `paper/alpaca/monthly_eval.py` month-end evaluator
- `paper/alpaca/install_scheduler.ps1` Windows task installer
- `paper/alpaca/install_research_scheduler.ps1` Windows task installer for nightly search
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

Connectivity + dry-run pipeline smoke on a sampled universe:
```powershell
conda run -n alpaca-paper python paper/alpaca/smoke_test.py --pipeline --date 2026-03-13
```

Generate daily signal file only:
```powershell
conda run -n alpaca-paper python paper/alpaca/signal_generator.py
```
Defaults come from `paper/alpaca/.env`, including `ALPACA_SIGNAL_MODEL`.

Bootstrap cached classifications from FMP:
```powershell
conda run -n alpaca-paper python paper/alpaca/classification_sync.py --snapshot-date 2026-03-17
```

Generate the profitability + asset gate model:
```powershell
conda run -n alpaca-paper python paper/alpaca/signal_generator.py --model profit_asset_gate --fundamentals-file paper/alpaca/private/reference/fundamentals.csv --classifications-file paper/alpaca/private/reference/classifications.csv
```

Generate the price-only proxy for profitability + asset gate:
```powershell
conda run -n alpaca-paper python paper/alpaca/signal_generator.py --model profit_asset_gate_proxy
```
The proxy model requires a cached classification snapshot under `paper/alpaca/private/reference/`.

Build/refresh universe file only (TOP3000-style liquid proxy):
```powershell
conda run -n alpaca-paper python paper/alpaca/universe_builder.py --max-symbols 3000 --shortable-only
```

Run the classification-aware proxy backtest:
```powershell
conda run -n alpaca-paper python paper/alpaca/backtest_runner.py --end-date 2026-03-16 --feed sip --train-days 1008 --oos-days 252 --test-days 252
```

Run the full staged research sweep:
```powershell
conda run -n alpaca-paper python paper/alpaca/research_runner.py --end-date 2026-03-16 --feed sip --alpha-set wave1
```

Run the resumable nightly search pipeline:
```powershell
conda run -n alpaca-paper python paper/alpaca/search_runner.py --new-run
```

Resume the latest nightly search:
```powershell
conda run -n alpaca-paper python paper/alpaca/search_runner.py --resume
```

Check latest search status:
```powershell
conda run -n alpaca-paper python paper/alpaca/search_runner.py --status
```

Run full daily pipeline (generate signal + rebalance):
```powershell
conda run -n alpaca-paper python paper/alpaca/daily_pipeline.py
```
With the recommended `.env`, this runs `profit_asset_gate_proxy` with `ALPACA_BOOK_MODE=sector`.

If `paper/alpaca/private/selected_strategy.json` exists and is approved, the daily pipeline automatically uses it without changing CLI flags. The promoted strategy can also be forced explicitly with:
```powershell
conda run -n alpaca-paper python paper/alpaca/daily_pipeline.py --model research_selected
```

Run the profitability + asset gate pipeline with raw balanced book construction:
```powershell
conda run -n alpaca-paper python paper/alpaca/daily_pipeline.py --model profit_asset_gate --fundamentals-file paper/alpaca/private/reference/fundamentals.csv --classifications-file paper/alpaca/private/reference/classifications.csv --book-mode none
```

Run the price-only proxy pipeline with raw balanced book construction:
```powershell
conda run -n alpaca-paper python paper/alpaca/daily_pipeline.py --model profit_asset_gate_proxy --book-mode none
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

Install the nightly research task:
```powershell
powershell -ExecutionPolicy Bypass -File paper/alpaca/install_research_scheduler.ps1
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
- Gross exposure: 4.0 total gross by default (2.0 long, 2.0 short)
- Book mode: dollar-neutral + sector-matched book by default; optional raw balanced book with `ALPACA_BOOK_MODE=none` or `--book-mode none`
- Margin guard: pre-trade incremental exposure is capped by
  `buying_power * ALPACA_BP_UTILIZATION - ALPACA_MARGIN_BUFFER_NOTIONAL`.
- Execution order priority: de-risking/flattening orders are sent before new risk-open orders.
- Strict target match: symbols held but missing from today target are added as synthetic `flat` targets and closed.
- Short execution: whole-share `qty` market orders (avoids Alpaca fractional short restriction)
- Reject retry policy: runner retries up to `ALPACA_MAX_RETRY_PASSES` total passes.
- Short reject correction: rejected short symbols are dropped from target and both sides are re-neutralized before retry.
- If rejections remain after max retries, run is marked `success_with_rejects` and unresolved symbols are logged.
- Kill switch: skip new entries if prior daily return <= -2%
- Skip-day semantics: skip statuses hold positions; no forced flatten.
- Cost model: 5 bps round-trip for proxy metric accounting
- Missed runs: logged, never backfilled late

## Data coverage (important)

The automated signal generator always uses Alpaca market data fields:
- `open`, `high`, `low`, `close`, `vwap`, `volume`

The default `failed_move_vwap` model uses only those fields.

The `profit_asset_gate_proxy` model is a price-only approximation:
- `profit_raw`: rolling mean return divided by rolling return volatility
- `asset_raw`: negative rolling return volatility
- `mom_raw`: rolling mean return
- group keys prefer cached `industry`, then cached `sector`, then `ALL`
- the proxy requires a cached classification snapshot created by `classification_sync.py`
- optional `sector_map.csv` can still backfill missing sector labels for individual names

The `profit_asset_gate` model also requires external point-in-time reference files:
- `paper/alpaca/private/reference/fundamentals.csv`
- `paper/alpaca/private/reference/classifications.csv`

Expected reference schemas:
- `fundamentals.csv`: `symbol,effective_date,fnd2_ebitdm,fnd2_ebitfr,fn_assets_fair_val_a`
- `classifications.csv`: `symbol,effective_date,sector,industry`

Starter templates:
- `paper/alpaca/fundamentals.example.csv`
- `paper/alpaca/classifications.example.csv`

The runtime does not source WorldQuant-style non-price fields from Alpaca directly.

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
- cached proxy classifications from `paper/alpaca/private/reference/classifications_latest.csv`
- cached symbol master from `paper/alpaca/private/reference/symbol_master.csv`
- optional `paper/alpaca/private/reference/fundamentals.csv` and `paper/alpaca/private/reference/classifications.csv` for `profit_asset_gate`
- `profit_asset_gate_proxy` requires `classification_sync.py` to run successfully at least once before live runs or backtests

If sector map is missing, the legacy price-only generator uses `sector=ALL`.

Recommended runtime defaults in `paper/alpaca/.env`:
- `APCA_DATA_FEED=sip`
- `FMP_API_KEY=...`
- `ALPACA_CLASSIFICATION_SOURCE=fmp`
- `ALPACA_SIGNAL_MODEL=profit_asset_gate_proxy`
- `ALPACA_BOOK_MODE=sector`
- `ALPACA_GROSS_EXPOSURE=4.0`

Research outputs are written under:
- `paper/alpaca/private/research_runs/<run_stamp>/`

Nightly search outputs are written under:
- `paper/alpaca/private/search_runs/<run_id>/`
- cross-run candidate cache: `paper/alpaca/state/research_cache.db`

Each run writes:
- `family_leaderboard.csv`
- `candidate_leaderboard.csv`
- `oos_survivors.csv`
- `unseen_results.csv`
- `candidate_correlation.csv`
- `promotion_report.md`
- `selected_strategy.json`
- `selected_strategy_daily_equity.csv`
- `selected_strategy_daily_positions.csv`

Each nightly search run writes:
- `search_state.json`
- `candidate_queue.jsonl`
- `screen_results.csv`
- `stability_expand_results.csv`
- `full_validation_results.csv`
- `mutation_results.csv`
- `search_report.md`
- `winner_summary.json`
- `shadow_strategy.json`

An approved winner is promoted to:
- `paper/alpaca/private/selected_strategy.json`

The nightly search never overwrites `paper/alpaca/private/selected_strategy.json`. Its best non-promoted artifact is copied to:
- `paper/alpaca/private/shadow_strategy.json`

Feed note:
- `APCA_DATA_FEED=sip` is used for historical bars in universe building, signal generation, and backtests.
- The rebalance runner's latest-price snapshots still use Alpaca's best available feed for order sizing and margin guard, because recent SIP snapshots may require a paid entitlement.

Starter templates:
- `paper/alpaca/universe.example.csv`
- `paper/alpaca/sector_map.example.csv`
- `paper/alpaca/fundamentals.example.csv`
- `paper/alpaca/classifications.example.csv`
