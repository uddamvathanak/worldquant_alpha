# worldquant_alpha

Metadata-first alpha research workspace for:
- field encyclopedia building
- hypothesis tracking
- template mapping
- external metric logging (fitness, margin, etc.)
- qualitative journal (why it worked / failed + economic intuition)

Local backtesting is intentionally removed for now.

## Install

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## Initialize

```bash
wqa init
```

Creates:
- `hypotheses`
- `logs`
- `reports`
- `docs`
- `knowledge`

## Core Workflow

1. Create a simulation-ready idea package (hypothesis + expression + fixed settings):
```bash
wqa propose-run ^
  --title "VWAP reversion" ^
  --rationale "Close below VWAP may revert next day" ^
  --economic-hypothesis "Intraday flow dislocation mean reverts cross-sectionally" ^
  --behavioral-mechanism "Close pressure from one-sided flow partially reverses next day" ^
  --risk-hypothesis "Crowded momentum regimes can suppress reversion" ^
  --failure-modes "Strong trend days and news shocks can dominate microstructure reversion" ^
  --expression "rank(vwap/close)" ^
  --fields-used "vwap,close,volume" ^
  --template-id "TPL_REV_001" ^
  --settings-profile baseline_d1
```

This creates:
- a hypothesis ID
- a simulation brief markdown in `reports/`
- a copy-ready `wqa log-result` command template

2. Build or update field encyclopedia entries from pasted notes:
```bash
wqa upsert-field ^
  --field vwap ^
  --category Price ^
  --description "Volume weighted average transaction price" ^
  --alpha-use-cases "Close-vs-flow dislocation, intraday pressure" ^
  --data-quality-checks "Check consistency with volume and price scale" ^
  --notes "Useful in short-horizon mean reversion"
```

If you copied raw text from the BRAIN interface, parse in bulk:
```bash
@'
vwap: Volume weighted average transaction price
close: Session closing price
volume: Traded shares in session
'@ | wqa import-fields-text --default-category Price --dry-run
```

Then save it:
```bash
@'
vwap: Volume weighted average transaction price
close: Session closing price
volume: Traded shares in session
'@ | wqa import-fields-text --default-category Price --notes "pasted from BRAIN"
```

3. Query field encyclopedia:
```bash
wqa fields --query vwap
```

4. Add a hypothesis directly (optional alternative to `propose-run`):
```bash
wqa add-hypothesis ^
  --title "VWAP reversion" ^
  --rationale "Close below VWAP may revert next day" ^
  --expression "rank(vwap/close)" ^
  --fields-used "vwap,close,volume" ^
  --template-id "TPL_REV_001" ^
  --setting-notes "baseline_d1"
```

5. Link hypothesis -> templates -> settings in a plan file:
```bash
wqa plan-hypothesis --hypothesis-id <HYPOTHESIS_ID> --infer-fields
```

6. Update economic/risk/failure annotations any time:
```bash
wqa annotate-hypothesis --hypothesis-id <HYPOTHESIS_ID> --economic-hypothesis "..." --behavioral-mechanism "..." --risk-hypothesis "..." --failure-modes "..."
```

7. Log external result metrics from platform runs:
```bash
wqa log-result ^
  --hypothesis-id <HYPOTHESIS_ID> ^
  --simulation-id "brain-sim-123" ^
  --settings-profile baseline_d1 ^
  --fitness 1.25 ^
  --margin 34.2 ^
  --sharpe 1.9 ^
  --turnover 0.42 ^
  --status keep ^
  --why-worked "Mean reversion stronger in recent regime" ^
  --why-failed "" ^
  --economic-intuition "Temporary dislocation between close and flow normalizes" ^
  --next-step "Check robustness in alternate universe"
```

8. Rank logged runs:
```bash
wqa leaderboard --sort-by fitness --limit 20
```

9. Inspect one run in full detail:
```bash
wqa show-run --run-id <RUN_ID>
```

## Other Useful Commands

- List hypotheses:
```bash
wqa list-hypotheses
```

- Suggest templates based on known fields:
```bash
wqa templates --fields vwap,close,volume --hypothesis-class MeanReversion --show-expression
```

- Show settings profiles:
```bash
wqa settings-profiles
```

## Alpaca Isolation

Alpaca paper-trading assets are isolated under `paper/alpaca/` to keep this public repo safe.

- folder guide: `paper/alpaca/README.md`
- conda setup runbook: `docs/ALPACA_PAPER_TRADING_CONDA_SETUP.md`
- daily runner: `paper/alpaca/rebalance_runner.py`
- monthly evaluator: `paper/alpaca/monthly_eval.py`
- scheduler installer: `paper/alpaca/install_scheduler.ps1`

Commit only public-safe code/templates. Keep credentials and runtime artifacts local.

## Documentation

- `docs/FASTEXPR_EVALUATION_NOTES.md`
- `docs/FIELD_TO_ALPHA_PIPELINE.md`
- `docs/FIELD_TO_ALPHA_TEMPLATE_MAP.md`
- `docs/FASTEXPR_OPERATIONS_REFERENCE.md`
- `docs/SETTINGS_KNOBS_PLAYBOOK.md`
- `docs/FIELD_ENCYCLOPEDIA_GUIDE.md`
- `docs/SIGNAL_FAMILIES.md`
- `docs/ALPACA_PAPER_TRADING_CONDA_SETUP.md`

## Important

Use only official platform capabilities and stay within account permissions and terms.
