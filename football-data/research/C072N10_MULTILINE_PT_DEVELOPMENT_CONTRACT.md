# C072-N10 — Multi-line O/U P(T) rolling-OOS development

## Lineage / classification
- Project: football3 only.
- Parent: C072-N9R1 development-only zero-label join correction.
- C073-C077 remain quarantined and are not ancestry/evidence/design input.
- This run is `REPLICATION / REPRODUCTION`, not independent confirmation, fresh evidence, blind test, or pristine confirmation, because the development labels/scientific area are globally consumed.
- formal_weight=0; no CURRENT change.

## Scientific question
Does the cross-threshold shape of closing O/U 0.5, 1.5, 2.5, 3.5, 4.5 materially improve complete match-level `P(T=0,1,2,3,4,5,6,7+)` beyond an otherwise identical model using only closing O/U2.5?

This is the exact N9 preregistered scientific comparison. No model/feature/threshold change is introduced after seeing labels.

## Immutable zero-label inputs
- N8 odds CSV from workflow run `32244931845`, artifact `football3-c072n8-multiline-odds-zero-label`, SHA256 `e9538997e1ec46582e240add8eb37372341a0c75b51e024c8bef0139aa29c082`.
- N9 join manifest from workflow run `32246282879`, artifact `football3-c072n9-zero-label-join`, SHA256 `027d7f4ff7cb72724115a731002d4d2048f10da420fdb8ac4955eb2782ba3a06`.
- Only N9 accepted mappings with `n8_Season` in 2015/16–2023/24 are eligible.

## Outcome source and strict target boundary
Pinned source: `nm2890/football-data` revision `279978313f9c16a210fa80e8986fa22f0f866fba`.
Fixed files are the same five N9 files.

For each source file, N10 may decode `FTHG` and `FTAG` **only** for row indices already present in the fixed N9 development manifest. Raw rows not selected by that manifest must be skipped before CSV field parsing. This mechanically prevents 2024/25 and 2025/26 score values from being decoded.

Target: `T=min(FTHG+FTAG,7)` with classes `[0,1,2,3,4,5,6,7+]`.

2024/25 target values materialized/read must remain exactly 0.
2025/26 target values materialized/read must remain exactly 0.
C070-F Confirmation1597/protected remain sealed.

## Market representation
For each valid two-sided O/U line L in {0.5,1.5,2.5,3.5,4.5}:
`q_L = (1/O_L) / ((1/O_L) + (1/U_L))`, clipped only to `[1e-6,1-1e-6]` for numerical logit safety.
`market_logit_L = log(q_L/(1-q_L))`.

Rows must have valid finite odds >1 on all ten O/U prices. The same paired rows are used for baseline and candidate.

Baseline numeric features:
- `market_logit_25`

Candidate numeric features:
- `market_logit_05`
- `market_logit_15`
- `market_logit_25`
- `market_logit_35`
- `market_logit_45`

Shared control:
- `sourceCode` league one-hot only.

Forbidden:
- 1X2/H-D-A features;
- BTTS;
- score-history;
- opening/closing movement;
- team-name encoding;
- manual curvature/interactions;
- Draw/0-0/1-1/T=2 boost or penalty.

## Frozen model family
Both models use the same pipeline:
- numeric median imputation;
- StandardScaler;
- `sourceCode` OneHotEncoder(handle_unknown='ignore');
- multinomial LogisticRegression;
- `C=0.1`;
- `max_iter=3000`;
- `class_weight=None`;
- `random_state=0`;
- no hyperparameter/model/feature/subset search.

## Chronological OOS folds
Exactly five expanding folds:
- test 2019/20; train 2015/16–2018/19
- test 2020/21; train 2015/16–2019/20
- test 2021/22; train 2015/16–2020/21
- test 2022/23; train 2015/16–2021/22
- test 2023/24; train 2015/16–2022/23

No random split. No same/future season enters training.

## Metrics
Primary:
- multiclass LogLoss, delta = candidate - baseline.

Secondary proper scores:
- multiclass Brier;
- RPS.

Secondary decision metrics:
- Top1 accuracy;
- Top3 accuracy.

Diagnostics only:
- fraction Top1 total = 2;
- per-class recall/LogLoss;
- per-league LogLoss deltas;
- entropy/top1 margin;
- probability residual.

Paired bootstrap:
- resample match rows;
- 3000 reps;
- seed 72009;
- 90% CI for pooled candidate-minus-baseline LogLoss.

## Frozen PASS gate
`C072N10_MULTILINE_PT_DEVELOPMENT_PASS` requires ALL:
1. all five chronological folds execute with nonzero paired test rows;
2. pooled dLogLoss < 0;
3. paired bootstrap90 upper dLogLoss < 0;
4. at least 4/5 chronological folds improve LogLoss;
5. pooled dBrier <= 0;
6. pooled dRPS <= 0;
7. pooled Top1 accuracy delta >= 0;
8. pooled Top3 accuracy delta >= 0;
9. at least 4/5 source leagues improve pooled LogLoss;
10. probability-conservation residual <=1e-12;
11. 2024/25 target values read/materialized = 0;
12. 2025/26 target values read/materialized = 0.

If FAIL: PARK this exact multi-line representation on these viewed labels. Do not change C, features, thresholds, line subset, fold windows, league subset, imputation/scaling, or gates and rerun as the same experiment.

If PASS: the result remains development reproduction only. A separately frozen globally unconsumed confirmation source is mandatory before any independent-evidence claim.

## Hard boundaries
- No 2024/25 or 2025/26 score/result access.
- No C070-F Confirmation1597 access.
- No protected asset access.
- No C073-C077 result used for model choice or interpretation beyond mandatory global-consumption classification.
- No exact-score or Draw promotion from this component alone.
