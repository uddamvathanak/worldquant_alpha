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
pip install alpaca-py pandas numpy python-dotenv pydantic
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
```

Notes:
- Use **paper** keys, not live keys.
- Never commit `paper/alpaca/.env` to git.

## 4) Minimal connectivity smoke test

Run the built-in smoke test:

```powershell
python paper/alpaca/smoke_test.py
```

Expected output includes account metadata (account number, status, buying power, equity).

## 5) Public repo safety model

Do:
- commit public-safe code and docs under `paper/alpaca/`
- commit templates like `paper/alpaca/.env.example`
- keep operational notes generic and non-sensitive

Do not:
- commit `paper/alpaca/.env`
- commit private strategy details under `paper/alpaca/private/`
- commit runtime logs/state/database/signal files

## 6) Signal input format

Daily signal file path:
- `paper/alpaca/signals/YYYY-MM-DD.csv` (ET date)

Required CSV columns:
- `symbol`
- `score`
- `sector`

Optional CSV column:
- `asof_date`

## 7) Daily run command

```powershell
conda run -n alpaca-paper python paper/alpaca/rebalance_runner.py
```

Dry-run mode (no order submission):
```powershell
conda run -n alpaca-paper python paper/alpaca/rebalance_runner.py --dry-run
```

Run a specific date:
```powershell
conda run -n alpaca-paper python paper/alpaca/rebalance_runner.py --date 2026-02-25
```

## 8) Install Windows task scheduler job

```powershell
powershell -ExecutionPolicy Bypass -File paper/alpaca/install_scheduler.ps1
```

Task defaults:
- name: `WQA_Alpaca_Rebalance_0935ET`
- schedule: weekdays 09:35 (local system timezone)
- wake-to-run: on
- network required: on
- retries: 2 (10-minute interval)
- time limit: 20 minutes

## 9) Month-end proxy evaluation

```powershell
conda run -n alpaca-paper python paper/alpaca/monthly_eval.py --month 2026-02
```

This writes:
- `paper/alpaca/logs/summary_YYYY-MM.csv`
- copy-ready `wqa log-result` command printed in terminal

## 10) Suggested project layout for paper trading phase

- `paper/alpaca/smoke_test.py`
- `paper/alpaca/rebalance_runner.py`
- `paper/alpaca/monthly_eval.py`
- `paper/alpaca/install_scheduler.ps1`
- `paper/alpaca/.env.example`
- `paper/alpaca/signals/`
- `paper/alpaca/logs/`
- `paper/alpaca/state/`
- `paper/alpaca/tmp/`
- `paper/alpaca/private/`

## 11) Operational rules before running continuously

- Add a hard kill switch by max daily drawdown.
- Add notional and symbol concentration limits.
- Add retry logic with idempotency around order submission.
- Record every order and fill locally for audit.
- Run a scheduled heartbeat check for connectivity.
