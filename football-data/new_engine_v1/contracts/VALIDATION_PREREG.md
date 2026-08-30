# Football3 New Engine V1 — Historical Validation Preregistration

Status: FROZEN_BEFORE_IMPLEMENTATION_AND_HOLDOUT_LABEL_READ
Anchor: `7c1815c47102412e88f72189e2b8f837d9b73a42`

## Chronological partition
After deterministic eligibility filtering and sorting by exact kickoff then fixture ID, use the earliest 60% as development state/history, the next 20% as tuning/selection, and the latest 20% as the final time-out holdout. Boundaries move forward to avoid splitting an identical kickoff timestamp. The final holdout is never used to select parameters or features.

V1 has a compact, predeclared parameter set. Tuning may select only among the small grid declared in code before holdout scoring: decay half-life, prior strength, xG blend weight, and cross-season shrink factor. Selection is based on tuning-set mean LogLoss with Brier/RPS as tie guards. No holdout-dependent search is allowed.

## Same-match comparison
New Engine and legacy comparison must be scored on the intersection of their eligible predictions with identical fixture IDs and target cutoffs. Coverage is reported separately; no model may gain an accuracy advantage by silently dropping difficult fixtures.

## Metrics
Primary probability metrics: 1X2 LogLoss, Brier and RPS. Secondary: Top1 accuracy, calibration/ECE, draw identification, underdog-win identification, coverage and failure rate.

Required slices: competition, cold-start/effective-history bucket, new-season rounds 1/2/3/29/30 where inferable without future schedule information, promoted/new-competition/sparse-team groups when deterministically identifiable from prior history, and worst sufficiently-sized subgroup.

## Promotion gate
The final holdout may write `MODEL_CANDIDATE_PASSED` only if ALL of these hold on the same-match comparison:
- New Engine LogLoss <= legacy LogLoss - 0.002.
- New Engine Brier <= legacy Brier - 0.001.
- New Engine RPS <= legacy RPS - 0.0005.
- Top1 accuracy is not worse than legacy by more than 0.25 percentage points.
- New Engine eligible coverage >= 99.0% of legal holdout fixtures and not worse than legacy coverage by more than 0.25 percentage points.
- No sufficiently-sized competition/cold-start subgroup (n>=100) has LogLoss degradation > 0.03 versus legacy.
- Draw binary LogLoss is not worse than legacy by > 0.005.
- Underdog-win Top1 recall is not worse than legacy by > 2.0 percentage points where the subgroup contains at least 100 events.
- Fail-closed, same-kickoff batching, no-market-in-pure-core, source-lineage and prediction-freeze integrity tests all pass.

If any required condition fails or cannot be measured honestly, scientific status is `NOT_PROMOTED`.

## Market-assisted lane
Market-assisted results are reported separately and never determine whether the pure model secretly consumed market data. If no verified PIT odds source is available for a fixture, that fixture is `MARKET_ASSIST_NOT_SCORED`; no closing-odds backfill is permitted.

## Forward protocol
After historical OOS completion, create a prospective capture registry with checkpoints 30/100/300. Predictions are frozen before results; 30 is operational-only, 100 trend-only, 300 stability confirmation. This protocol does not alter the historical promotion gate.