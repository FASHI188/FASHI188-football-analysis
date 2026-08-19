# C074-D frozen research contract

Status: research-only / formal_weight=0 / development probe, not confirmation.

## Question
Does free public O/U2.5 opening-to-closing movement contain stable incremental information for the complete direct total-goals distribution P(T=0,1,2,3,4,5,6,7+) beyond the opening O/U2.5 state itself?

## Source
- Public no-login repository: `nm2890/football-data`.
- Pinned revision: `279978313f9c16a210fa80e8986fa22f0f866fba`.
- C074-C source gate: 40,934 valid open+close O/U2.5 rows across 8 leagues and 16 seasons.
- Source limitation is binding: opening/closing semantics exist, but immutable per-row quote timestamps do not. Therefore C074-D can only be research evidence and can never be described as a formal timestamped multi-line market snapshot.

## Target
`T = min(FTHG + FTAG, 7)` with class 7 meaning 7+ goals. Eight classes total.

## Features
For each row, de-vig the O/U2.5 pair:

`p_over = (1/over_odds) / ((1/over_odds) + (1/under_odds))`

Then clip to [1e-6, 1-1e-6] and compute logit.

Baseline numeric feature:
- `open_logit = logit(p_over_open)`

Candidate adds exactly one scalar:
- `movement_logit = logit(p_over_close) - logit(p_over_open)`

Both models additionally use the same categorical control:
- `league_key = country + '|' + league`

No team identities, score-history features, xG, lineups, 1X2, BTTS, alternative OU lines, nonlinear transforms, interactions, feature search or subsets are allowed in this probe.

## Algorithm
Same fixed pipeline for baseline and candidate except for the one added movement scalar:
- numeric StandardScaler
- league OneHotEncoder(handle_unknown='ignore')
- multinomial LogisticRegression, L2, C=0.1, solver=lbfgs, max_iter=2000, no class weights

No C search is allowed.

## Time-ordered OOS folds
Expanding-window training, one-season test folds:
1. test 2019-2020; train all valid seasons strictly earlier
2. test 2020-2021; train all valid seasons strictly earlier
3. test 2021-2022; train all valid seasons strictly earlier
4. test 2022-2023; train all valid seasons strictly earlier
5. test 2023-2024; train all valid seasons strictly earlier

2024-2025 is excluded because the public mirror is partial at the pinned revision. No random train/test split.

## Primary metrics
On pooled OOS rows and per fold:
- exact-T LogLoss
- multiclass Brier score
- ordered RPS over T=0..7+

Diagnostics:
- exact-T Top-1 accuracy
- exact-T Top-3 accuracy
- AUC for T>=3 using the sum of class probabilities 3..7+

## Paired uncertainty
3,000 game-level paired bootstrap resamples, RNG seed 20260819, on per-row candidate-minus-baseline LogLoss. Report 90% interval.

## Frozen pass gate
C074-D is `MOVEMENT_INCREMENT_PASS` only if all are true:
1. pooled candidate-minus-baseline exact-T LogLoss < 0
2. candidate exact-T LogLoss wins at least 4 of 5 chronological folds
3. pooled candidate-minus-baseline Brier <= 0
4. pooled candidate-minus-baseline RPS <= 0
5. paired bootstrap 90% upper bound for dLogLoss < 0

Otherwise terminal is `MOVEMENT_INCREMENT_NOT_ESTABLISHED`.

No alternative C, feature transform, time folds, league subsets, movement thresholds or post-view candidate variants may be tried on these viewed labels under C074-D.

## Governance boundaries
- This uses public development labels only; it does not open C071 reserve 52,180, C070-F Confirmation 1,597, A05 or protected assets.
- C073-A D|T remains untouched.
- Even a PASS does not promote formal weight and does not satisfy CURRENT's formal timestamped market-snapshot requirement.
