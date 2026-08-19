# C072-E2 — O/U2.5 opening-to-closing movement Direct-T development

## Lineage and isolation
- football3 only.
- Parent scientific checkpoint: C072-C @ `e3e73c998020beef585cc459a69ea5b73b44ddb3`.
- Source gate: C072-D2 @ `2b72e19a6c00cf92262a8da24894d5dfe3ced6ca`, `SOURCE_COVERAGE_PASS_COARSE_PIT_ONLY`.
- C073-C077 are quarantined and are not evidence, design input, stopping-rule input, or interpretation input.
- formal_weight=0; no CURRENT change.

## Scientific question
Conditional on a fixed score-history Direct-T baseline **and the O/U2.5 opening market level**, does the opening-to-closing O/U2.5 movement add stable out-of-sample information for complete `P(T=0..7+)`?

This is a research-grade coarse-PIT test only. The source has opening/closing semantics but no immutable quote timestamps and no multi-line total-goals surface.

## Source
`nm2890/football-data` pinned revision `279978313f9c16a210fa80e8986fa22f0f866fba`, eight fixed league CSVs from C072-D2.

## Target and chronology
- `T = min(FTHG + FTAG, 7)`, eight classes `0,1,...,6,7+`.
- Same-date matches are all predicted before same-date results update history.
- A target is eligible only when both teams have >=8 strictly prior result-history matches.
- Development test seasons are exactly start-years `2019, 2020, 2021, 2022, 2023` (2019/20 through 2023/24).
- Each fold trains only on eligible matches with season start-year strictly earlier than the test season.
- Calendar/season 2024/25 target goal values are **sealed** and must not be parsed/materialized/scored in C072-E2. Its identities/odds may be counted only for confirmation-presence integrity.

## Frozen score-history baseline representation
Exactly 12 features, mirroring the C072 score-history baseline concept:
1. competition_total_mean
2. competition_total_sd
3. home_goals_for_mean
4. home_goals_for_sd
5. home_goals_against_mean
6. home_goals_against_sd
7. away_goals_for_mean
8. away_goals_for_sd
9. away_goals_against_mean
10. away_goals_against_sd
11. log1p_home_result_history_n
12. log1p_away_result_history_n

No same-date result may enter these histories.

## Market features
Opening de-vig Over probability:
`p_open = (1/over_open) / ((1/over_open) + (1/under_open))`

Closing de-vig Over probability:
`p_close = (1/over_close) / ((1/over_close) + (1/under_close))`

Use clipped logits with epsilon `1e-8`:
- `open_logit = logit(p_open)`
- `movement_logit = logit(p_close) - logit(p_open)`

## Models
All use:
- `SimpleImputer(strategy='median')`
- `StandardScaler()`
- `LogisticRegression(C=0.1, solver='lbfgs', max_iter=3000, class_weight=None, random_state=0)`
- no hyperparameter search, class weights, thresholds, interactions, league subset selection, or nonlinear movement transform.

Models:
- diagnostic score-history baseline: 12 BASE features only.
- **reference**: BASE + `open_logit`.
- **candidate**: BASE + `open_logit` + `movement_logit`.

The primary estimand is candidate minus reference, so movement must add information beyond both score history and opening market level.

## Frozen coverage gates
Before effect interpretation, every development fold must have:
- training rows >= 15,000
- test rows >= 1,500
- all eight target classes represented in training
- >=6 source leagues represented in test
- candidate/reference probability rows finite and normalized.

Pooled development test rows must be >=8,000.

## Metrics
Primary: multiclass exact-T LogLoss.
Secondary proper scores: multiclass Brier, normalized RPS.
Diagnostics: Top1 accuracy, Top3 accuracy, AUC(T>=3), AUC(T>=4), top-label ECE.

Paired match bootstrap on candidate-minus-reference LogLoss:
- 3,000 reps
- seed 72022
- 90% interval.

## Frozen development PASS gate
`C072E2_MOVEMENT_DEVELOPMENT_PASS` only if ALL hold:
1. coverage gates pass;
2. pooled candidate-minus-reference dLogLoss < 0;
3. paired bootstrap 90% upper bound dLogLoss < 0;
4. pooled dBrier <= 0;
5. pooled dRPS <= 0;
6. >=4/5 chronological test seasons have dLogLoss < 0;
7. max probability-sum residual <=1e-10.

Otherwise terminal is `C072E2_MOVEMENT_INCREMENT_NOT_ESTABLISHED` (or `STOP_COVERAGE` if coverage fails).

Top1/AUC/ECE cannot override proper-score failure.

## Stopping rule
If development fails, do not on these scored labels:
- change C;
- change opening/movement transforms;
- add other odds/BTTS/1X2 fields;
- change seasons/folds;
- select league subsets;
- search interactions/nonlinearities;
- manually boost Draw/1-1 or score classes.

Any such idea is a new hypothesis requiring a genuinely new data plan.

## If development passes
Do **not** call it confirmation. Freeze a separate C072-F2 one-shot 2024/25 confirmation contract before parsing any 2024/25 goal values. The exact C072-E2 architecture, C, transforms, history rule and confirmation gates must be written first.

## Hard boundaries
- C072-C viewed 959 labels not reused for tuning.
- C073-C077 quarantined.
- C070-F Confirmation1597 sealed.
- protected samples sealed.
- 2024/25 target goals sealed in this stage.
- no exact-score matrix, Draw boost, 1-1 boost, EV output, or formal promotion.
