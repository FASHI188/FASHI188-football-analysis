# C074-G Frozen Contract — Football-Data.co.uk 2025/26 Forward Direct-T Confirmation

Status: independent forward confirmation of the C074-E research component. `formal_weight=0` until every later promotion/PIT/runtime gate separately passes.

This contract is frozen **after C074-F zero-label source PASS and before any 2025/26 score/result label is materialized**.

## Confirmation question
Does the already-frozen C074-E market-movement increment improve the full `P(T=0..6,7+)` distribution on a genuinely later 2025/26 football season, after conditioning on the same score-history Direct-T information architecture?

## Confirmation target/domain
Source: public no-login Football-Data.co.uk CSV files.

Frozen test season: `2025/26` only.
Frozen divisions:
- E0 England Premier League
- SP1 Spain La Liga
- I1 Italy Serie A
- D1 Germany Bundesliga
- F1 France Ligue 1
- N1 Netherlands Eredivisie
- B1 Belgium First Division A

C074-F established, without reading result labels, 2,369 complete-valid 2025/26 identity+O/U2.5 open/close rows across all 7 leagues.

Target after this contract is frozen: `T=min(FTHG+FTAG,7)`, eight classes `0,1,2,3,4,5,6,7+`.

## Frozen external-source adapter
Football-Data's post-2019/20 columns are mapped deterministically:
- earlier/open-stage O/U2.5 market average: `Avg>2.5`, `Avg<2.5`
- closing O/U2.5 market average: `AvgC>2.5`, `AvgC<2.5`

`movement_logit = logit(devig_over(AvgC>2.5, AvgC<2.5)) - logit(devig_over(Avg>2.5, Avg<2.5))`.

No other market price, bookmaker, line, interaction, transform, threshold, or selection is permitted.

## Frozen score-history baseline
Same C074-E information architecture:
- competition/league prior total-goals mean and population sd;
- home team's strictly prior goals-for mean/sd and goals-against mean/sd;
- away team's strictly prior goals-for mean/sd and goals-against mean/sd;
- `log1p` strictly-prior result-history counts for home and away;
- both teams require at least 8 strictly prior league results for eligibility;
- all fixtures on the same calendar date are featurized/predicted before any result from that date updates team/competition history.

Team history is keyed by Football-Data division + team string, eliminating cross-provider name reconciliation.

## Frozen historical backfill and model training
To preserve long score-history context, retrieve score-only identity/results for the seven frozen divisions from 2009/10 onward when the source file exists. Missing old score-only files are logged, never replaced by result-dependent selection.

The **model fitting sample** is fixed to complete-valid, eligible Football-Data rows from seasons `2019/20` through `2024/25`, because Football-Data documents both earlier/open-stage and closing odds from 2019/20 onward.

The **confirmation sample** is every complete-valid, eligible row from `2025/26`; no league/row is dropped based on its target or model error.

Minimum execution requirements before any scientific verdict:
- >= 6 of 7 frozen leagues represented in training;
- >= 6 of 7 frozen leagues represented in 2025/26 confirmation;
- >= 8,000 eligible training rows;
- >= 1,800 eligible 2025/26 confirmation rows;
- all eight T classes present in training and confirmation.
Failure of these is `STOP_DATA/COVERAGE`, not a scientific fail.

## Frozen model
Baseline: C074-E BASE score-history features only.
Candidate: identical baseline + exactly one `movement_logit` scalar.

Pipeline for both:
`median imputation -> StandardScaler -> multinomial LogisticRegression(C=0.1, solver=lbfgs, no class weights, random_state=0, max_iter=3000)`.

No C search, calibration tuning, feature search, history-window search, league subset search, movement transform search, or post-label repair.

## Metrics
Primary: multiclass exact-T LogLoss, candidate minus baseline.
Secondary proper scores: multiclass Brier and normalized RPS.
Diagnostics: Top-1, Top-3, AUC(T>=3), probability conservation, top-label ECE, T>=3 ECE and calibration slope/intercept, per-league LogLoss delta, class frequencies.

Paired match bootstrap: 5,000 resamples, seed `20260819`, 90% interval for candidate-minus-baseline exact-T LogLoss.

## Frozen CONFIRMATION_PASS gate
All must hold:
1. pooled confirmation `dLogLoss < 0`;
2. paired-bootstrap 90% upper bound for `dLogLoss < 0`;
3. pooled `dBrier <= 0`;
4. pooled `dRPS <= 0`;
5. at least 4 of the 7 frozen leagues with >=100 eligible confirmation rows have `dLogLoss < 0`;
6. probability conservation max absolute residual <= `1e-10`.

Top-1, Top-3, AUC and calibration are diagnostic and cannot override the proper-score confirmation gate.

## Interpretation rule
PASS => `CONFIRMATION_PASS` for the C074-E **research component only**. It still does not establish formal timestamped multi-line OU, formal model weight, production Direct-T, exact tail disaggregation, D|T, or a unified exact-score matrix. Formal promotion remains separately gated by CURRENT and main/PIT runtime requirements.

FAIL => confirmation fails. No tuning, feature repair, C changes, subset selection or transform search is allowed on the 2025/26 labels.

## Protected assets
C071 reserve52180, C070-F Confirmation1597, A05 and all protected samples remain untouched.
