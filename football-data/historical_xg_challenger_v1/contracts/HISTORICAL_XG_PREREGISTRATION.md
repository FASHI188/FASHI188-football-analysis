# Football3 Historical XG Challenger V1 — Validation Preregistration

Status: `FROZEN_BEFORE_MODEL_IMPLEMENTATION`

## Frozen candidate grid

Cartesian product, exactly 54 candidates:

- `dynamic_half_life_days`: `[90, 180, 360]`
- `dynamic_prior_matches`: `[4, 8, 16]`
- `dynamic_beta`: `[0.05, 0.10, 0.15]`
- `dynamic_cross_season_shrink`: `[0.40, 0.70]`

Fixed constants for every candidate:

- `xg_pseudocount = 0.25`
- `residual_clip = 0.75`
- `min_effective_evidence = 3.0`
- `pooled_prior_weight = 0.50`
- result/xG release delay = `3 hours`

Candidate-grid canonical SHA256: `5dda62fd564bf3953d247e65ea10384798c2ef7eb53d278cdb2bf4617e91a62f`.

## Time partition

The public frozen xG universe is Big-5 Understat seasons 2014–2023, sorted by source `date ASC, fid ASC`.

- Warm-up: seasons 2014–2017, `n=7303`. No candidate selection score.
- Candidate selection: seasons 2018–2019, `n=3551`. Select exactly one candidate by minimum LogLoss; tie-break Brier, RPS, then lexicographic parameter tuple.
- Outer validation: seasons 2020–2022, `n=5478`, split into eight contiguous exact-kickoff-batch folds. Selection is frozen before these folds are scored.
- Historical confirmation: season 2023, `n=1752`, only if every development/outer gate passes. No parameter or structure change is allowed before confirmation.

Outer folds are frozen by exact source timestamp batch count:

1. fold0: 394 batches, n=671, `2020-08-21 17:00:00` through `2020-12-20 19:45:00`
2. fold1: 394 batches, n=666, `2020-12-20 20:00:00` through `2021-03-15 20:00:00`
3. fold2: 394 batches, n=707, `2021-03-17 14:00:00` through `2021-09-18 19:00:00`
4. fold3: 394 batches, n=690, `2021-09-19 10:30:00` through `2022-01-02 20:00:00`
5. fold4: 393 batches, n=656, `2022-01-03 17:30:00` through `2022-04-20 18:45:00`
6. fold5: 393 batches, n=737, `2022-04-20 19:00:00` through `2022-10-16 14:15:00`
7. fold6: 393 batches, n=640, `2022-10-16 15:05:00` through `2023-02-25 13:00:00`
8. fold7: 393 batches, n=711, `2023-02-25 14:30:00` through `2023-06-04 19:00:00`

## Development promotion gates

All must pass, with frozen V1 as the only baseline:

1. At least `6/8` outer folds have Challenger LogLoss <= V1 LogLoss.
2. Pooled outer LogLoss gain `V1 - Challenger >= 0.001`.
3. Pooled Brier Challenger <= V1.
4. Pooled RPS Challenger <= V1.
5. Top1 delta `Challenger - V1 >= -0.0015`.
6. Every competition×season group with `n>=100` has LogLoss degradation `Challenger - V1 <= 0.020`.
7. For every `n>=100` competition×season group whose realized home-goal mean exceeds away-goal mean, Challenger predicted mean home goals must exceed predicted mean away goals.
8. PIT, same-kickoff predict-before-update, identity, duplicate-fixture, same-team-in-batch, future-release and exact-fallback deterministic tests all pass.

If any development gate fails, terminal status is exactly `HISTORICAL_XG_CHALLENGER_REJECTED`; 2023 historical confirmation must not be scored and no rescue tuning is allowed.

## Frozen historical confirmation gates

Only after all development gates pass, replay 2023 season once with the already-selected candidate. All must pass for `HISTORICAL_XG_CHALLENGER_CANDIDATE_PASSED`:

- pooled LogLoss gain `V1 - Challenger >= 0.001`;
- Brier not worse;
- RPS not worse;
- Top1 delta >= `-0.0015`;
- every `n>=100` competition×season group LogLoss degradation <= `0.020`;
- home-direction gate passes;
- all PIT/identity/fallback guards pass.

If development passed but any confirmation gate fails, final status is `HISTORICAL_XG_CHALLENGER_REJECTED`.

No result may be described as prospective. A pass remains research-only and `formal_weight=0`.
