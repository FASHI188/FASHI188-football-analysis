# C074-E Frozen Contract — Score-History + O/U2.5 Movement Direct-T Bridge

Status: research-only / `formal_weight=0` / development conditional-increment bridge. This is not confirmation and cannot change any formal weight.

## Scientific question
Does the already-fixed C074-D coarse O/U2.5 open→close movement signal retain incremental information for the complete `P(T=0..6,7+)` distribution after conditioning on the previously frozen C072-B PIT score-history Direct-T baseline family?

## Frozen source
- Public no-login source: `nm2890/football-data`
- Pinned revision: `279978313f9c16a210fa80e8986fa22f0f866fba`
- Source boundary remains unchanged from C074-C/D: average opening/closing O/U2.5 semantics only; no immutable per-row original quote timestamp; one O/U2.5 line only. Therefore all results are research-level coarse-PIT evidence, not a formal synchronized market snapshot.

## Estimand
Direct 8-class total-goals distribution: `T=min(FTHG+FTAG,7)`, classes `0,1,2,3,4,5,6,7+`.

## Frozen baseline
Reuse the C072-B score-history baseline structure without feature search:
- competition total-goals historical mean and sd;
- home team prior goals-for mean/sd and goals-against mean/sd;
- away team prior goals-for mean/sd and goals-against mean/sd;
- `log1p` prior result-history counts for home and away teams.

History is strictly chronological. All fixtures on the same calendar date are featurized/predicted before any result from that date updates team or competition history. Team history is keyed within league. Both teams require at least 8 strictly prior result matches before a target row is eligible.

Model family is frozen to the C072-B family: median imputation → standardization → multinomial logistic regression, `C=0.1`, `lbfgs`, no class weights, no hyperparameter search.

## Candidate
Exactly baseline + one already-fixed C074-D scalar:
`movement_logit = logit(devig_over_close) - logit(devig_over_open)`.

No opening-price level, alternate movement transform, bookmaker subset, league subset, interaction, threshold, window, C value, feature replacement, or fold variant may be searched after target scoring.

## Frozen OOS folds
Same five expanding one-season folds used in C074-D:
- 2019-2020
- 2020-2021
- 2021-2022
- 2022-2023
- 2023-2024

For each fold, train on eligible seasons strictly earlier than the test season; test only the named season.

## Metrics
Primary: exact-T multiclass LogLoss, candidate minus baseline.

Secondary proper scores: multiclass Brier and normalized RPS.

Required diagnostics: Top-1, Top-3, AUC(T>=3), probability conservation, classwise one-vs-rest calibration intercept/slope, top-label ECE and T>=3 ECE.

Paired match bootstrap: 3000 resamples, seed `20260819`, 90% interval for candidate-minus-baseline LogLoss.

## Frozen scientific PASS gate
All must hold:
1. pooled `dLogLoss < 0`;
2. at least 4/5 chronological folds have `dLogLoss < 0`;
3. pooled `dBrier <= 0`;
4. pooled `dRPS <= 0`;
5. paired-bootstrap 90% upper bound for `dLogLoss < 0`.

Top-1/Top-3/AUC/calibration are diagnostic and cannot override the primary proper-score gate.

## Stopping rule
This is the one authorized conditional bridge on the already-viewed C074-D development domain. After its result, do not tune C, history windows, transformations, thresholds, league subsets, movement variants, or alternate feature combinations on these labels. If PASS, the next authorized step is an untouched later public period or genuinely unseen external/forward domain with the exact frozen method. If FAIL, this bridge is not repaired on the same labels.

## Protected assets
This experiment must not open or score C071 reserve 52,180, C070-F Confirmation 1,597, A05, or any protected/fixed confirmation sample.
