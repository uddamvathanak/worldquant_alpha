# Web Factor Sources (2026-02)

This note captures public references used to seed factor motifs for `cycle_v2`.

## Source Registry

1. 101 Formulaic Alphas (Kakushadze, 2016)
- URL: https://arxiv.org/abs/1601.00991
- Usage in this repo: motif inspiration for short-horizon price/volume/range interactions and diversified formula search.
- Mapping notes:
  - `price_volume_corr` -> `open`, `close`, `volume`, `ts_corr`, `ts_delta`
  - `range_close_location` -> `high`, `low`, `close`
  - `cross_sectional_ranking` -> `rank(...)` and blended weighted legs
- Provenance tag: `source_web`

2. AlphaEvolve (Lemke et al., 2021)
- URL: https://arxiv.org/abs/2103.16196
- Usage in this repo: methodology reference for broad expression search and reducing local overfitting around one family.
- Mapping notes:
  - Supports policy to run structured candidate batches under fixed settings.
  - Supports mutation discipline after initial batch (single controlled mutation only).
- Provenance tag: `source_web`

3. AlphaCFG (Liu et al., 2026)
- URL: https://arxiv.org/abs/2601.22119
- Usage in this repo: methodology reference for grammar-constrained alpha generation and validity checks.
- Mapping notes:
  - Supports compile-safe motif decomposition by leg.
  - Supports fallback-by-leg policy instead of ad hoc full-expression rewrites.
- Provenance tag: `source_web`

## Practical Constraints

- These sources are used for motif design and search process only.
- They are not treated as proof that any specific WorldQuant BRAIN field is available in your account.
- Field availability and operator support are validated by BRAIN compile/simulation outcomes.
- If web evidence is insufficient for a direct field mapping, use local validated fields and log `source_ref=local`.

## Mandatory Provenance Keys for Run Logs

Include these keys in `wqa log-result --notes`:
- `source_ref`
- `motif`
- `fallback_used`

Example:

```text
source_ref=https://arxiv.org/abs/1601.00991;motif=price_volume_corr;fallback_used=none
```
