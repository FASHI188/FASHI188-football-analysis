# E3f-2A Internal PIT H/D/A Ablation Contract

## Status

- Research only.
- Pure 90-minute H/D/A isolation track.
- `formal_weight=0`.
- Draft PR only; do not merge.
- This stage evaluates internally derived PIT features only.
- Feature groups: 7.
- Model types: 2.
- Candidate model specifications: 14.
- External records ingested: 0.
- Class weights: 0.
- Post-hoc thresholds: 0.

## Fixed identities

- Source base: E3f-1B exact HEAD `5842f1a42c75a56f9a34fadda7ae85016c4e674c`.
- Internal feature builder: E3f-1A exact schema, 93 features.
- Full sample: the frozen 6,251 Big-Five rolling-OOS matches.
- Benchmark: the fixed B100, 20 matches per league.
- Random split, sample replacement and target-season fitting are forbidden.

## Feature groups

1. `A_MARKET`: existing pre-match market features.
2. `B_LEGACY_TEAM`: existing non-market team/model-state features.
3. `C_LEGACY_COMBINED`: market plus legacy team features; reproduces the E3e-0 combined family.
4. `D_TASK_REST`: season-reset standings, points/goal-difference state, rest days and 7/14-day congestion.
5. `E_STYLE_STATE`: prior-only rolling shots/SOT/corners/cards proxies and coarse HT-to-FT response proxies.
6. `F_INTERNAL_ALL`: all 93 E3f-1A internal features.
7. `G_MARKET_INTERNAL`: market plus all 93 internal features.

No external source record is permitted.

## Models

Each group is evaluated with the same pre-registered E3e-0 estimators:

- linear Logistic;
- histogram gradient-boosted tree.

The two-stage structure is fixed:

- `q=P(Draw|X)`;
- `r=P(Home|Non-Draw,X)`;
- `P(D)=q`;
- `P(H)=(1-q)r`;
- `P(A)=(1-q)(1-r)`.

No class weights, oversampling, manual draw uplift, post-hoc threshold or target-season hyperparameter selection is allowed.

## Time order

- Strict rolling OOF by season start year.
- Only earlier seasons may train the target season.
- Same-day features are frozen before any result from that day updates state.
- The first non-trainable fold uses the pre-registered market fallback for market groups and Champion fallback for non-market groups.

## Required metrics

For every group and model:

- draw PR-AUC with bootstrap interval;
- ROC-AUC and calibration;
- Top 5/10/15/20 candidate Precision and Recall;
- Precision >=30% and >=35% diagnostic recall;
- H/D/A Accuracy, Balanced Accuracy and Macro-F1;
- draw Precision, Recall and F1;
- LogLoss, Brier and RPS;
- H/D/A prediction counts and proportions;
- actual draws called as H/D/A;
- full sample, every league, every season and B100.

Baselines:

- market;
- Champion;
- E3b-1;
- E3d-1;
- E3e-0 combined family through `C_LEGACY_COMBINED`.

## Incremental gates

Two paired comparisons are pre-registered for each model type:

1. `G_MARKET_INTERNAL` versus `A_MARKET`;
2. `F_INTERNAL_ALL` versus `B_LEGACY_TEAM`.

A diagnostic candidate may advance only when all are true:

- paired bootstrap 95% lower bound of full-sample PR-AUC delta is above zero;
- Top-10 candidate Precision is not lower;
- H/D/A LogLoss, Brier and RPS each remain within 0.5% of the paired baseline;
- PR-AUC delta is positive in at least 3 of 5 leagues;
- PR-AUC delta is positive in at least 2 of modeled seasons 2023-2025;
- B100 PR-AUC delta is non-negative.

Passing this gate does not issue promotion and does not set a formal weight.

## Protection and non-goals

This stage must not:

- join external records;
- modify formal model, data, config, CURRENT or weights;
- create score, total-goal or BTTS outputs;
- tune a draw threshold;
- use class weights;
- issue a promotion receipt;
- merge the PR.

## Stop condition

After the complete ablation and stability report, stop for review. No automatic external-source pilot, threshold change, formalization or follow-on training is authorized.
