# C072-I2 — one-shot external 2025/26 confirmation of Model A P(H|T,X)

## Lineage / isolation
- football3 only.
- C072-H2 development selected exactly `MODEL_A_OPENING` at `b33f24501dd4e8a31a1963701b2ee226c67bddde`.
- Model B movement is disqualified from confirmation because its frozen development Top1 increment was negative despite proper-score improvement.
- C073-C077 remain quarantined and provide no evidence, design input, thresholds, stopping rules or interpretation.

## First/only fresh target opening
C072-G2 froze a zero-label 2025/26 pool of exactly 4,184 identities across 12 Football-Data divisions:
`E1,E2,E3,SC0,SC1,SC2,SC3,D2,I2,SP2,F2,P1`.
Its score/result values have not been materialized in football3.

C072-I2 is the first and only authorized opening of those 2025/26 target scores for the H2 conditional-allocation hypothesis.
No missing target may be replaced by another row.

## Confirmation estimand
For realized exact total `T=1..6`, predict legal home-goal allocation `H=0..T`, equivalent to `D=2H-T`.
`T=0` is deterministic and excluded from component scoring. `T>=7` remains unresolved and is not collapsed into exact-score output.

## Frozen candidate: H2 Model A only
For every T=1..6, fit a separate multinomial model with exactly:
- the 12 H2 strict-PIT score-history BASE features;
- `open_strength = log(pH_open/pA_open)`;
- `open_drawness = log(pD_open/sqrt(pH_open*pA_open))`;
- `SimpleImputer(median) + StandardScaler + LogisticRegression(C=.1, solver=lbfgs, max_iter=3000, class_weight=None, random_state=0)`.

No closing/movement feature is permitted in C072-I2.
No C/feature/transform/model-family/search/league-selection/calibration/blend/Draw boost/score boost is permitted.

## Frozen portable baseline
For each T=1..6 use pooled training-domain empirical `P(H|T)` with Dirichlet alpha=1 smoothing on legal support H=0..T. No confirmation-domain or league-specific baseline fitting.

## Model-coefficient training domain
Fit baseline tables and all six Model-A coefficient sets once using the same H2 development source:
- `nm2890/football-data` revision `279978313f9c16a210fa80e8986fa22f0f866fba`;
- eight historical leagues;
- all eligible rows with season start-year <=2023;
- same-date predict-before-update;
- both teams need >=8 strictly prior result-history matches.

No 2024/25 or 2025/26 lower-division target is used to fit coefficients.

## Confirmation feature-history warmup
To avoid treating the 2025/26 season as history-free, use Football-Data 2024/25 files for the same 12 division codes solely to initialize score-history features and competition total history.
- 2024/25 warmup scores may be read only after this confirmation contract is frozen.
- They are not scored, not used to choose candidate architecture, not used to refit candidate coefficients, and not used to alter gates.

During 2025/26 confirmation, earlier-date 2025/26 results may update feature histories for later-date targets, but:
- all matches on the same date are predicted before any same-date update;
- model coefficients and empirical baseline tables remain frozen from the H2 training domain;
- no confirmation outcome changes any parameter.

## 1X2 opening source in confirmation
Use Football-Data average opening prices `AvgH, AvgD, AvgA`; de-vig mechanically to `(pH,pD,pA)` and compute the exact H2 opening coordinates above.
Closing prices are ignored by the candidate.

## Source identity gate before effect interpretation
The 2025/26 input must reproduce:
- 12/12 fixed divisions downloadable;
- exactly 4,184 raw identities;
- duplicate `(division,Date,HomeTeam,AwayTeam)` = 0;
- no replacement rows.

Any identity drift => `STOP_CONFIRMATION_IDENTITY_DRIFT`; do not score an altered pool.

## Coverage gate before effect interpretation
After strict-PIT feature construction:
- pooled eligible T=1..6 confirmation rows >=3,000;
- all 12 divisions represented;
- each division has >=80 eligible T=1..6 rows;
- each T=1..5 has >=100 confirmation rows;
- T=6 has >=40 confirmation rows;
- H2 training population for each T has >=200 rows and all legal H classes;
- candidate and baseline probability vectors finite, nonnegative and normalized.

Failure => `STOP_CONFIRMATION_COVERAGE`; do not lower thresholds or replace rows.

## Metrics
Primary: conditional exact-H LogLoss per match.
Secondary: conditional multiclass Brier and normalized RPS.
Ranking: conditional Top1 and support-capped Top3.
Diagnostics: even-T D=0/Draw Top1 calls, precision and recall.

Paired match bootstrap of candidate-minus-baseline LogLoss:
- 5,000 reps;
- seed 72026;
- 90% interval.

Frozen robustness partitions:
1. early and late chronological halves of eligible confirmation rows, split by ordered row count;
2. all 12 source divisions;
3. exact T=1..6.

## All-required confirmation PASS gate
`C072I2_DGIVENT_FORWARD_CONFIRMATION_PASS` only if ALL hold:
1. identity and coverage gates pass;
2. pooled candidate-minus-baseline dLogLoss <0;
3. bootstrap90 upper bound <0;
4. pooled dBrier <=0;
5. pooled dRPS <=0;
6. pooled dTop1 >=0;
7. both chronological halves have dLogLoss <0;
8. strict majority of the 12 divisions have dLogLoss <0 (>=7/12);
9. at least 5/6 exact-T groups have dLogLoss <0;
10. max probability-sum residual <=1e-10.

Otherwise `C072I2_DGIVENT_CONFIRMATION_FAIL_PARK`.
Top3 or Draw diagnostics cannot rescue a proper-score/Top1 gate failure.

## One-shot stopping rule
After the 2025/26 target scores are opened, do not repair the hypothesis on this pool. Forbidden: C changes, closing/movement additions, coordinate changes, alternate history windows, class weights, per-T family changes, league deletions, recalibration, blend, Draw/0-0/1-1 boosts, threshold changes, row replacement.

A PASS confirms only T=1..6 conditional allocation. T>=7 remains an explicit unresolved blocker for a complete exact-score matrix.

## Hard boundaries
- C070-F Confirmation1597 remains sealed.
- protected samples remain sealed.
- C073-C077 quarantined.
- formal_weight=0; no CURRENT change.
- no unified exact-score matrix, EV output, artificial Draw/0-0/1-1 boost.
