# B05 eladsil 1X2 trajectory → Direct-T R1 preregistration

## Boundary

- Research-only; `formal_weight=0`.
- Exactly one new sealed package may be consumed: global alias `B05`.
- This preregistration binds `B05` to prior zero-label reserve batch `ELAD-PIT6H-B001` from run `31991609157`, artifact `9275514166`.
- Expected batch size: 400.
- Expected original batch-manifest SHA-256: `094edba4ae1e4955ef174f790011a4f49dc7013d3c51a842f64f70a7590f2f4e`.
- Expected source `Matches_Odds.csv` SHA-256: `bfa14f07a11581ffd793b1f44d3e7c9203c631d26ac1c692fe71a77f66579298`.
- B01–B04 are already VIEWED and are not reused here.
- B06 and all later packages remain sealed; no result values from them may be semantically dereferenced.
- The preflight stage may inspect CSV member names and header rows only; it must not read any result data row.
- Settlement is forbidden until this preregistration and an immutable zero-label feature packet have both been frozen.

## Scientific question

Does the **pre-match path of the 1X2 market** contain information about total-goal state `T` beyond the final available 1X2 snapshot at least six hours before kickoff?

This is intentionally a new-information test, not another selector-shell test on the already-VIEWED PR204 rows.

## PIT contract

For each B05 match, use only `Matches_Odds.csv` observations satisfying:

`date_start - date_created >= 6 hours`.

Odds must be finite and strictly greater than 1.0. At least two distinct valid timestamps are required by the sealed-reserve contract.

At duplicate timestamps, de-vigged H/D/A probabilities are aggregated by componentwise median and then renormalized to sum to one. Observations are then ordered strictly by `date_created`.

## Frozen features

For decimal odds `(oH,oD,oA)`, compute raw inverse probabilities and de-vig:

`p = (1/oH,1/oD,1/oA) / sum(1/oH,1/oD,1/oA)`.

### Baseline snapshot features

The baseline receives only the final valid T-6h observation:

1. `last_log_H_over_D = log(pH/pD)`
2. `last_log_A_over_D = log(pA/pD)`
3. `last_pD`
4. `last_entropy = -sum(p*log(p))`
5. `last_quote_hours_before_kickoff`

### Challenger trajectory additions

The challenger receives all baseline features plus exactly these zero-label trajectory features:

1. `first_log_H_over_D`
2. `first_log_A_over_D`
3. `delta_log_H_over_D = last - first`
4. `delta_log_A_over_D = last - first`
5. `range_pH`
6. `range_pD`
7. `range_pA`
8. `std_pH`
9. `std_pD`
10. `std_pA`
11. `slope_log_H_over_D_per_hour`
12. `slope_log_A_over_D_per_hour`
13. `log1p_distinct_timestamps`
14. `trajectory_span_hours`

Slopes are ordinary least-squares slopes against elapsed hours from the first valid observation. If elapsed-time variance is zero, preflight must fail closed.

No feature may use match result, score, total goals, post-kickoff data, bookmaker settlement state, or information from another sealed package.

## Target and evaluation

Primary target is five-class total goals:

- class 0: `T=0`
- class 1: `T=1`
- class 2: `T=2`
- class 3: `T=3`
- class 4: `T>=4`

B05 is sorted by kickoff time, then stable `match_id` tie-break.

Chronological expanding OOS protocol:

- rows 1–100: warm-up/training only;
- rows 101–200: OOS fold 1, train rows 1–100;
- rows 201–300: OOS fold 2, train rows 1–200;
- rows 301–400: OOS fold 3, train rows 1–300.

Thus 300 matches are scored OOS. There is no hyperparameter search and no fold selection.

Both models use the same fixed shell:

`SimpleImputer(strategy='median', keep_empty_features=True) -> StandardScaler -> LogisticRegression(C=0.1, penalty='l2', solver='lbfgs', max_iter=4000, multi_class via sklearn default)`.

Class labels absent from an early training fold are handled by the model's observed classes; predicted probabilities are expanded back into the fixed five-class space with zero for unseen classes and clipped to `[1e-12,1]` before normalization/scoring. If fewer than two target classes exist in a training fold, fail closed.

## Primary metric and gate

Primary metric: five-class multiclass log loss over the 300 chronological OOS predictions.

Define `delta_LL = LL_challenger - LL_baseline`; negative is better.

Paired uncertainty: 10,000 bootstrap resamples of **kickoff-date clusters**, seed `2026081705`, recomputing the paired mean per-match log-loss delta.

A **development signal** is established only if all are true:

1. `delta_LL < 0`;
2. bootstrap 90% upper bound for `delta_LL` is `< 0`;
3. challenger multiclass Brier score is not worse than baseline;
4. challenger ranked probability score (RPS) is not worse than baseline.

Secondary descriptive metrics: top-1 accuracy, per-class log loss, 4+ one-vs-rest Brier/AUC when both classes are present. Secondary metrics cannot rescue a failed primary gate.

## Interpretation boundary

- PASS means only that B05 provides an OOS **development signal** that 1X2 trajectory carries incremental total-goal information beyond the final T-6h snapshot in this fixed construction.
- FAIL closes only this frozen construction; it does not prove all odds dynamics are useless.
- No result authorizes formal promotion, CURRENT/main mutation, draw-solution claims, or opening B06 automatically.
- No post-result tuning on B05 is allowed. Any altered features/model/target require a new preregistration and a later still-sealed package.
