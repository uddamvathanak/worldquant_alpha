# Field -> Hypothesis -> Alpha Pipeline

This is the working system for turning data knowledge into stronger alphas while keeping settings controlled.

## Core Principle

Maximize objective quality by tracking:
- `fitness` (primary rank metric from platform output)
- `margin` (secondary efficiency metric from platform output)

Always compare alphas under the same settings profile.

## Linked Artifacts

- Field encyclopedia: `knowledge/field_encyclopedia.csv`
- Alpha template map: `knowledge/alpha_template_map.csv`
- Settings profiles: `knowledge/settings_profiles.csv`
- Hypothesis registry: `hypotheses/hypotheses.jsonl`
- Run metrics DB: `logs/experiments.db`
- Notes:
  - `docs/FASTEXPR_EVALUATION_NOTES.md`
  - `docs/FASTEXPR_OPERATIONS_REFERENCE.md`
  - `docs/SETTINGS_KNOBS_PLAYBOOK.md`

## Workflow

1. Explore fields
```bash
wqa fields --query vwap
```

2. Map fields to candidate templates
```bash
wqa templates --fields vwap,close,volume --hypothesis-class MeanReversion --show-expression
```

3. Register hypothesis with explicit field linkage
```bash
wqa add-hypothesis ^
  --title "VWAP close reversion" ^
  --rationale "Close below VWAP may mean revert" ^
  --expression "rank(vwap/close)" ^
  --fields-used "vwap,close,volume" ^
  --template-id "TPL_REV_001" ^
  --setting-notes "baseline_d1"
```

4. Auto-generate hypothesis plan markdown
```bash
wqa plan-hypothesis --hypothesis-id <ID> --infer-fields
```

5. Log external metrics with fixed settings profile
```bash
wqa log-result ^
  --hypothesis-id <ID> ^
  --simulation-id "brain-sim-123" ^
  --settings-profile baseline_d1 ^
  --objective fitness_margin ^
  --fitness 1.2 ^
  --margin 35 ^
  --sharpe 1.8 ^
  --turnover 0.45 ^
  --status keep ^
  --why-worked "..." ^
  --why-failed "..." ^
  --economic-intuition "..." ^
  --next-step "..."
```

6. Rank runs by objective
```bash
wqa leaderboard --sort-by fitness --limit 30
```

## Promotion Rules (Suggested)

- Keep:
  - positive `fitness`
  - positive `margin`
  - controlled turnover
  - acceptable drawdown
- Promote only after passing at least:
  - multiple time periods
  - at least one alternate universe/region check
  - stability under small decay/truncation perturbations
