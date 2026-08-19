# C072-H2 — conditional home-goal allocation P(H|T,X) development

## Lineage / isolation
- football3 only.
- Parent: C072-G2 fresh 2025/26 zero-label pool PASS @ `21bf6e4114d021f2e76d6ddc7285d8605b0c0bf6`.
- C073-C077 remain quarantined and provide no hypothesis/model/metric/source/stopping-rule input.
- Fresh C072-G2 2025/26 score values remain sealed throughout this development.

## Scientific target
For exact total goals `T`, predict the legal allocation `H=home goals`, equivalent to `D=2H-T`.

This stage covers `T=1..6` only.
- `T=0` is deterministic H=0 and needs no model.
- `T>=7` remains unresolved and cannot be silently collapsed into an exact-score matrix.

## Development source
Pinned historical `nm2890/football-data` revision `279978313f9c16a210fa80e8986fa22f0f866fba`, the same eight leagues already consumed for C072 P(T) work.
Only seasons through 2023/24 are used for C072-H2 scoring. 2024/25 is not used for D|T development.

Because these historical result labels have been consumed for a different P(T) task, C072-H2 is development evidence only. The independent scientific adjudication is reserved for the zero-label C072-G2 2025/26 lower-division pool.

## Strict-PIT score-history representation
Same-date matches are predict-before-update. Both teams need >=8 strictly prior result matches.

12 fixed BASE features:
- competition_total_mean, competition_total_sd
- home_goals_for_mean, home_goals_for_sd, home_goals_against_mean, home_goals_against_sd
- away_goals_for_mean, away_goals_for_sd, away_goals_against_mean, away_goals_against_sd
- log1p_home_result_history_n, log1p_away_result_history_n

## Fixed 1X2 coordinates
For opening and closing average 1X2 prices, de-vig to `(pH,pD,pA)`.
Use two independent coordinates:
- `strength = log(pH/pA)`
- `drawness = log(pD/sqrt(pH*pA))`

Frozen market features:
- `open_strength`, `open_drawness`
- `move_strength = close_strength-open_strength`
- `move_drawness = close_drawness-open_drawness`

No alternative transforms/interactions are allowed in this development.

## Baseline and two nested models
### Empirical baseline
For each exact T, training-fold pooled `P(H=h|T)` over h=0..T with Dirichlet alpha=1 smoothing. No league conditioning, to preserve portability to unseen confirmation divisions.

### Model A — OPENING
For each T=1..6 fit a separate legal-support multinomial model:
- features = BASE + open_strength + open_drawness
- SimpleImputer(median) + StandardScaler + LogisticRegression(C=.1, solver=lbfgs, max_iter=3000, class_weight=None, random_state=0)

### Model B — MOVEMENT
Same per-T model and hyperparameters, with exactly two added features:
- move_strength
- move_drawness

No C search, feature search, class weights, Draw boost, score boost, league subset selection, nonlinear transform, or per-T hyperparameter changes.

## Rolling OOS folds
Test season start-years exactly: 2019, 2020, 2021, 2022, 2023.
Each fold trains only on season start-year strictly less than its test season.

Coverage before effect interpretation:
- each fold pooled T1..6 test rows >=1,800;
- each fold total training rows T1..6 >=15,000;
- for every T=1..6, training rows >=200 and every legal H class 0..T is observed;
- pooled test rows >=10,000.

## Metrics
Primary conditional exact-H LogLoss, averaged per match across T1..6.
Secondary: multiclass Brier and normalized RPS on each legal T support, conditional Top1.
Diagnostics: Top3 (capped by support), even-T draw Top1 recall/precision.

Paired match bootstrap:
- 3,000 reps
- seed 72024
- 90% interval.

Report pooled metrics, five chronological folds and pooled results by exact T.

## Frozen qualification rules
### Model A qualifies (`OPENING_QUALIFIES`) iff ALL:
- coverage passes;
- A minus empirical baseline pooled dLogLoss <0;
- bootstrap90 upper <0;
- dBrier <=0;
- dRPS <=0;
- dTop1 >=0;
- >=4/5 chronological folds have dLogLoss<0;
- >=5/6 exact-T pooled groups have dLogLoss<0;
- probability closure max residual <=1e-10.

### Model B movement increment qualifies (`MOVEMENT_INCREMENT_QUALIFIES`) iff ALL:
- B minus A pooled dLogLoss <0;
- bootstrap90 upper <0;
- dBrier <=0;
- dRPS <=0;
- dTop1 >=0;
- >=4/5 folds have dLogLoss<0.

## Deterministic confirmation-candidate selection
- If A qualifies AND B movement increment qualifies: confirmation candidate = Model B.
- Else if A qualifies: confirmation candidate = Model A.
- Else: `C072H2_DGIVENT_DEVELOPMENT_FAIL_PARK`; no confirmation and no repair on these labels.

This selection rule is frozen before any C072-H2 D|T score is produced.

## Stopping rule
After development labels are scored, do not alter C, feature subsets, transforms, fold dates, T range, class weighting, empirical smoothing, league subsets, or model family to repair a failure.

## If a confirmation candidate is selected
Freeze a separate one-shot C072-I2 confirmation contract **before** any C072-G2 2025/26 score value is opened. The chosen architecture is copied exactly; no model-choice decision may be revisited after fresh labels.

## Hard boundaries
- C072-G2 2025/26 target results sealed.
- C070-F Confirmation1597 sealed.
- protected sealed.
- C073-C077 quarantined.
- formal_weight=0; no CURRENT change; no unified exact-score matrix; no artificial 0-0/1-1/Draw boosts.
