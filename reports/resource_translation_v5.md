# Resource Translation Note v5

Maps external-resource motifs to compile-safe Fast Expression candidates and explicit fallback behavior.

| Candidate | Source Ref | Motif | Primary Legs | Exploratory Leg | Fallback Used | Profile |
|---|---|---|---|---|---|---|
| D1-1 | `external_resources/alpha_ideas.md` | `price_volume_corr_plus_vwap` | `ts_corr(open,volume)`, `ts_corr(delta(volume), intraday_return)`, `vwap-close timing` | none | `none` | `v5_usa_d1_locked` |
| D1-2 | `external_resources/alpha_ideas.md` | `close_location_volume_plus_range` | `close-location`, `volume correlation`, `range reversion` | none | `none` | `v5_usa_d1_locked` |
| D1-3 | `external_resources/submitted_alphas.md` | `value_revision_news` | `revision`, `news accumulation` | `book_value_per_share / close` | `val=-rank(ts_zscore(enterprise_value / ebitda,126))` | `v5_usa_d1_locked` |
| D1-4 | `external_resources/alpha_ideas.md` | `vrp_skew_size_bucket` | `VRP`, `size-bucketed skew`, `range reversion` | none | `none` | `v5_usa_d1_locked` |
| D0-1 | `external_resources/alpha_ideas.md` | `failed_move_vwap` | `intraday return`, `vwap extension`, `product term` | none | `none` | `v5_usa_d0_locked` |
| D0-2 | `external_resources/alpha_ideas.md` | `intraday_pressure_volume_impulse` | `close pressure`, `volume impulse` | none | `none` | `v5_usa_d0_locked` |
| D0-3 | `external_resources/submitted_alphas.md` | `short_interest_crowding_plus_overextension` | `intraday overextension`, `vwap timing` | `short_interest` | `crowd=rank(ts_mean((ivp-ivc)/(ivp+ivc),5))` | `v5_usa_d0_locked` |
| D0-4 | `external_resources/submitted_alphas.md` | `earnings_price_rank_plus_flow` | `session flow spread`, `revision momentum` | `eps_ttm / close` (unavailable) | `applied_primary: earnp=rank(ts_delta(anl4_afv4_eps_high,20))` | `v5_usa_d0_locked` |

## Compile Policy

1. Compile all candidates as written.
2. If compile fails on exploratory leg, replace only that leg with the documented fallback.
3. Re-run compile with identical settings.
4. If still failing, mark `fallback_used=failed` and reserve replacement from same branch.
