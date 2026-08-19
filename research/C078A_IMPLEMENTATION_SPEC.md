# C078-A Frozen Implementation Spec

Frozen before any C078-A scientific execution. This file only disambiguates numerical indexing in the already-frozen C078-A contract; it does not add/remove a scientific model, metric or PASS gate.

## Evaluation support and residual

- numerical PMF grid: exact totals `0..60` inclusive;
- analytic residual: `P(T>=61)` from the fitted Poisson/NB2 survival function;
- conservation audit: `sum_{t=0}^{60} p(t) + P(T>=61)`;
- hard residual-mass gate remains `max P(T>=61) <= 1e-8` on scored rows.

No residual mass is renormalized into `T=60` or any other finite cell.

## Exact-count Brier

For realized totals `T<=60`:

`Brier = sum_{t=0}^{60} (p_t - 1[T=t])^2`.

Any realized `T>60` is an execution STOP because the frozen numerical evaluation grid would no longer contain the realized class; it is not replaced or clipped.

## Exact-count normalized RPS/CRPS

Use the 60 ordered thresholds `k=0..59`:

`RPS60 = (1/60) * sum_{k=0}^{59} (F(k)-1[T<=k])^2`.

The contract wording “over thresholds 0..60, divided by 60” is operationalized as the standard K-class convention: the 61 explicitly evaluated cells `0..60` have 60 internal cumulative thresholds `0..59`. Analytic mass beyond 60 is audited separately and is not collapsed into cell 60.

## Conditional exact-tail Brier/RPS

For realized tail rows `T>=7`, define `q_t=P(T=t)/P(T>=7)`.

- tail Brier numerical cells: `t=7..60`;
- conditional residual beyond 60 is reported and must remain finite; the full-distribution `P(T>=61)<=1e-8` hard gate remains controlling;
- tail RPS uses the 53 ordered thresholds `k=7..59` and is divided by 53.

## Bootstrap

All bootstraps are match-level iid resamples over the already-frozen OOS row set for the relevant estimand. No league/year stratified resampling, cluster substitution or seed variation is permitted in C078-A.

## Optimizer convergence

Poisson/NB2 each must return SciPy optimizer `success=True` in every fold. NB2 alpha-bound audit fails if fitted alpha is within relative/absolute numerical tolerance of either frozen bound: `alpha <= 1.0001e-6` or `alpha >= 9.999`.

## Categorical benchmark

The C074-E categorical baseline/candidate are reconstructed only on the exact same train/test row identities selected for the C078-A fold. No row can be added or dropped separately for a categorical comparator after C078-A row selection.
