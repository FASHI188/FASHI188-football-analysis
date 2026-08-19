# C072-F2 — O/U2.5 movement one-shot 2024/25 forward confirmation

## Lineage
- football3 only.
- C072-C parent: `e3e73c998020beef585cc459a69ea5b73b44ddb3`.
- C072-D2 zero-label source PASS: `2b72e19a6c00cf92262a8da24894d5dfe3ced6ca`.
- C072-E2 development PASS: `8dca316be2c4852853eead15ee92560e773ef318`.
- C073-C077 remain quarantined and cannot be used as evidence, design input, thresholds, stopping rules, or interpretation.

## Confirmation population
Pinned source `nm2890/football-data` revision `279978313f9c16a210fa80e8986fa22f0f866fba`.
C072-E2 established 781 zero-label identities with season start-year 2024 and parsed **zero** 2024/25 goal values.
This source snapshot is a **partial 2024/25 forward window**, not a claim of a complete season.

C072-F2 is the first and only permitted opening of those 2024/25 goal values for this hypothesis.

## Exact frozen model recipe
No changes from C072-E2:
- target `T=min(FTHG+FTAG,7)`;
- same-date predict-before-update history;
- both teams require >=8 prior result-history matches;
- 12 fixed score-history BASE features;
- `open_logit` from de-vig O/U2.5 opening prices;
- `movement_logit = logit(p_close)-logit(p_open)`;
- reference = BASE + open_logit;
- candidate = BASE + open_logit + movement_logit;
- `SimpleImputer(median) + StandardScaler + LogisticRegression(C=.1, solver=lbfgs, max_iter=3000, class_weight=None, random_state=0)`;
- no refit/tuning on confirmation effects, no feature search, no transform search.

Models are fit once on all eligible rows with season start-year <2024. During the 2024/25 forward window, historical score features may update only with matches whose dates are strictly earlier than the target date; same-date matches remain predict-before-update. Model coefficients are not re-estimated during confirmation.

## Coverage gate — checked before effect interpretation
- confirmation eligible rows >=600;
- test source leagues >=6;
- training rows >=30,000;
- training contains all eight T classes;
- probability rows finite and normalized;
- no target-row replacement if a 2024/25 result is missing.

If coverage fails: `STOP_CONFIRMATION_COVERAGE`; do not change the threshold or replace rows.

## Primary and secondary scores
Primary: candidate-minus-reference multiclass LogLoss.
Secondary: multiclass Brier and normalized RPS.
Diagnostics only: Top1, Top3, AUC(T>=3), AUC(T>=4), top-label ECE.

Paired match bootstrap:
- 5,000 reps;
- seed 72023;
- 90% interval.

Robustness partitions frozen before labels:
1. `early` and `late` chronological halves of confirmation eligible rows (split by ordered row count, not by outcome);
2. source-league clusters with >=50 eligible confirmation rows.

## All-required confirmation PASS gate
`C072F2_FORWARD_CONFIRMATION_PASS` only if ALL hold:
1. coverage gate passes;
2. pooled candidate-minus-reference dLogLoss <0;
3. bootstrap90 upper bound dLogLoss <0;
4. pooled dBrier <=0;
5. pooled dRPS <=0;
6. both chronological halves have dLogLoss <0;
7. a strict majority of eligible >=50-row league clusters have dLogLoss <0;
8. max probability-sum residual <=1e-10.

Otherwise `C072F2_CONFIRMATION_FAIL_PARK`.
Top1/AUC/ECE cannot rescue a proper-score failure.

## One-shot stopping rule
After confirmation goal values are opened, no repair on this population is allowed: no C change, no movement/opening transform change, no feature additions, no BTTS/1X2 additions, no league deletions, no time split changes, no recalibration/blending, no Draw/1-1 boost.

A PASS confirms only a **research component under coarse opening/closing PIT semantics**. Because quote timestamps and multi-line market depth are absent, formal_weight remains 0 and this cannot by itself generate a formal exact-score matrix.

## Hard boundaries
- C070-F Confirmation1597 remains sealed.
- protected samples sealed.
- C073-C077 quarantined.
- no CURRENT change, formal promotion, exact-score matrix, EV output, Draw boost, or 1-1 boost.
