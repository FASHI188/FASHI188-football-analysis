# Market6 GD0 Integration R6

## Why R6 exists

R5's formal pre-registered **combined53** development gate did not pass and remains a FAIL. However, the same frozen R5 report contained a prespecified secondary comparison in which the **market6-only** binary GD0 head beat the parent conditional multinomial p0 on all three even totals and all three rolling windows:

- pooled market6-only LL = 0.6629974852
- pooled parent p0 LL = 0.6765616264
- delta = -0.0135641412
- 24-cluster bootstrap 90% interval = [-0.02232334, -0.00567910]
- probability market6-only better = 0.9988

R6 tests whether that component-level signal survives the actual downstream probability geometry.

## Frozen integration

Direct-T is unchanged.

For each rolling test window, three market6-only binary heads are fit on chronological train+policy rows, separately within actual T=2, T=4 and T=6. The estimator and C are exactly the R5 market6-only contract: median imputer → StandardScaler → LogisticRegression(C=0.1), with no tuning.

For every market-complete target match and each hypothetical even total:

- T=2: replace parent P(GD=0|T=2,X) with market6 q0
- T=4: replace parent P(GD=0|T=4,X) with market6 q0
- T=6: replace parent P(GD=0|T=6,X) with market6 q0

All GD!=0 probabilities are rescaled proportionally, preserving the parent nonzero-GD shape exactly. T=0,1,3,5,7 are untouched.

No calibration layer, blend-weight search, forced Draw, HDA threshold or post-result parameter search is allowed.

## Two evaluation views

**Primary: market-complete rows.** Baseline and candidate are evaluated on identical rolling-test rows for which all six market values exist.

**Secondary: all rows with parent fallback.** On market-incomplete rows the candidate is exactly the parent model, so this shows the diluted whole-test effect without inventing missing market values.

## Frozen gates

Probability signal requires all:

- HDA LogLoss gain >= 0.003 on the primary market-complete cohort;
- competition-season cluster bootstrap LogLoss p95 < 0;
- Brier non-worse;
- RPS non-worse.

Draw activation separately requires all:

- Draw F1 gain >= 0.01;
- at least 10 additional natural Top-1 Draw calls;
- at least 3 additional correct Draw hits.

The overall descriptive development signal requires **both** probability quality and natural Draw activation. This prevents a small probability-only improvement from being mislabeled as solving the Draw problem.

## Evidence limits

The market columns are retrospective closing/reference evidence without original quote timestamps, so they are not strict PIT snapshots. The rolling labels are already VIEWED historical data.

Therefore even a positive R6 result is mechanism evidence only:

- formal_weight=0;
- no scientific/confirmation PASS claim;
- no formal promotion;
- B05-B07 remain unopened;
- no automatic future OOS label opening;
- no formal model/data/config/CURRENT/main mutation.
