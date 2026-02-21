# Settings Knobs Playbook

This guide explains what each common simulation knob does, with practical defaults and tuning logic.

Important:
- Treat defaults as starting points, not universal truth.
- Keep settings fixed while testing expression variants.
- Re-tune settings only after expression quality is stable.

## Knob Effects (Intuition)

- `Delay`
  - Lower delay reacts faster but is more fragile.
  - `Delay=1` is usually more robust for daily research.

- `Decay`
  - Higher decay smooths noise and lowers turnover.
  - Too high can kill alpha responsiveness.

- `Neutralization`
  - Removes broad/group exposure so signal reflects stock selection.
  - Stronger neutralization often improves robustness but may reduce raw return.

- `Truncation`
  - Caps extreme positions and reduces concentration risk.
  - Too tight can flatten signal differentiation.

- `Pasteurization`
  - Stabilization/sanitization style control for robustness.
  - Prefer enabled unless you have a specific reason not to.

- `NaN Handling`
  - Missing values treatment can materially change coverage and behavior.
  - Keep this fixed across comparisons.

- `Unit Handling`
  - Protects against incompatible units in mixed terms.
  - Keep strict/verify mode by default.

## Golden Rule Profiles

Use profiles in `knowledge/settings_profiles.csv`.

1. `baseline_d1`
- Good default for many short-horizon daily ideas.
- Suggested when exploring new hypotheses.

2. `slow_value_d1`
- Better for slower, fundamental-heavy expressions.
- Lower turnover and smoother behavior.

3. `fast_intraday_like`
- Faster response and usually higher turnover.
- Use only when the hypothesis explicitly needs high responsiveness.

## Practical Tuning Ladder

When `fitness` is weak:

1. Keep expression fixed.
2. Try `decay`: `2 -> 4 -> 6 -> 8`.
3. Try neutralization granularity: `Sector -> Subindustry`.
4. Try truncation range around baseline: `0.06 -> 0.08 -> 0.10`.
5. Re-check turnover and margin after each change.

When `margin` is weak:

1. Increase decay (reduce churn).
2. Tighten truncation slightly.
3. Simplify expression (remove fragile terms).
4. Verify liquidity assumptions and universe quality.

## Safety Guardrails

- Avoid optimizing all knobs together (overfitting risk).
- Change one knob at a time and log.
- Confirm robustness across time segments.
- Reject settings that only help in one narrow period.
