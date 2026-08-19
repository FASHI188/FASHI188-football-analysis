# C072-N12 — A-League dynamic multi-line O/U surface P(T) development

Project: football3 only
Scientific root: C072-C (`e3e73c998020beef585cc459a69ea5b73b44ddb3`)
Immediate parent: C072-N11 A-League dynamic O/U zero-label source PASS
Research stage: development, with future confirmation policy frozen before development labels
Formal weight: 0

## Scientific question
Does a complete **multi-line dynamic O/U probability surface** add stable match-level exact-total information beyond a strong **dynamic O/U2.5 trajectory** baseline?

This is deliberately not a retune of C072-N10. N10 tested a static closing five-line representation on a different viewed domain and PARKed. N12 uses a genuinely different provider-native time-series information family and a different competition domain.

## Source identity
Official public Betfair Data Scientists repository:
`betfair-datascientists/betfair-datascientists.github.io`
revision `9fe7fb127cd05316dbd438fe0e5be82c5c3ed536`.

Exact six men's A-League All Markets files and SHA256 values are frozen by C072-N11.

N11 established without target access:
- 992 non-conflict matches with preferred O/U;
- 944 matches with all five preferred lines complete at T-60/T-30/T-1;
- per-season all-five completeness 125/156/159/168/174/162 from 2020-21 through 2025-26;
- target/result values materialized = 0.

## Global-consumption classification
Before this contract, shared GitHub/Airtable history was searched for the exact Betfair Data Scientists source, A-League Betfair work, A-League 2025/26 and Australian A-League result experiments. No prior target-scoring experiment on these exact All Markets files or 2025/26 A-League domain was found.

A repository metadata registry mentions ten 2024/25 SkillCorner A-League tracking samples, but no scoring PR using those samples was found. This is retained as a caution, not treated as evidence that the exact Betfair 2025/26 target pool was consumed.

Development is classified `NEW_DOMAIN_DEVELOPMENT`. The 2025/26 source pool is **provisionally eligible for one-shot forward confirmation under the searched project history**, but it is not opened by N12. Any discovered exact identity-level consumption before N13 target access must be excluded/reclassified before opening labels; no replacement identities may be chosen using outcomes.

## Frozen target
Direct total goals:
`T = min(int(TOTAL_GOALS), 7)`
with classes `0,1,2,3,4,5,6,7+`.

`TOTAL_GOALS` is the only outcome field N12 may read. `HOME_SCORE`, `AWAY_SCORE`, `IS_WINNER`, `RUNNER_STATUS` and every other result/settlement field remain forbidden.

## Frozen modeling eligibility
A match may enter N12 development only if all are true under the N11 zero-label rules:
1. non-conflict EVENT_ID identity;
2. exactly one valid market per preferred line 0.5/1.5/2.5/3.5/4.5 after fail-closed duplicate handling;
3. exactly one Over and one Under runner per market;
4. all five preferred lines have valid uncrossed best-back/best-lay quotes at all three provider-native snapshots T-60, T-30 and T-1 minutes;
5. season is one of 2020-2021 through 2024-2025.

The paired baseline/candidate population is identical. No row may be added or removed after its target is read because of model performance.

2025-2026 identities and market fields may be audited, but N12 must not index or materialize their `TOTAL_GOALS` values.

## Frozen exchange quote transform
For each line `l` and snapshot `s`, for each runner r in {Over, Under}:

`a_r(l,s) = 0.5 * (1 / best_back_r(l,s) + 1 / best_lay_r(l,s))`

Then de-vig the pair:

`q_over(l,s) = a_over(l,s) / (a_over(l,s) + a_under(l,s))`

Clip only for numerical logit evaluation to `[1e-6, 1-1e-6]` and define:

`z(l,s) = log(q_over(l,s) / (1-q_over(l,s)))`.

This transform is fixed before target access. Matched volume is excluded from both baseline and candidate; N12 tests probability-surface information, not liquidity.

## Frozen baseline
Exactly 3 features:
- `z(2.5, T-60)`
- `z(2.5, T-30)`
- `z(2.5, T-1)`

This baseline contains O/U2.5 level and its full three-point prematch trajectory. It is intentionally strong; the candidate must beat dynamic O/U2.5, not a static or score-history-only baseline.

## Frozen candidate
Exactly 15 features:
`z(l,s)` for every
`l in {0.5,1.5,2.5,3.5,4.5}` and
`s in {T-60,T-30,T-1}`.

No explicit movement, slope, curvature, PCA, interaction, polynomial, bookmaker-volume, 1X2, BTTS, team identity, score history, Draw, 0-0, 1-1 or T=2 feature is added. Linear combinations of the three timepoint logits can represent movement without a separately tuned transform.

## Frozen estimator
Both baseline and candidate use the identical estimator:
- `StandardScaler`
- multinomial `LogisticRegression`
- `C=0.1`
- L2 penalty / default multinomial-compatible solver in pinned scikit-learn 1.7.1
- `max_iter=3000`
- `class_weight=None`
- `random_state=0` where accepted.

No C/model/feature/fold search.

If a training fold lacks one of the eight target classes, its probability is assigned fixed epsilon `1e-9` at alignment before renormalization for both baseline and candidate. This is a numerical support rule, not a learned tail model.

## Frozen chronological development folds
Only 2020-21 through 2024-25 targets may be read by N12.

Expanding OOS:
1. train 2020-21 -> test 2021-22
2. train 2020-21..2021-22 -> test 2022-23
3. train 2020-21..2022-23 -> test 2023-24
4. train 2020-21..2023-24 -> test 2024-25

No same-season training on the test season. No random split.

## Frozen metrics
Primary: pooled exact-T multiclass LogLoss.
Guard metrics:
- multiclass Brier score (sum of class squared errors per row, then mean)
- RPS over ordered 8-class totals, normalized by 7 cumulative boundaries
- exact-T Top1 accuracy
- exact-T Top3 accuracy.

Diagnostics only:
- per-class true-class LogLoss
- predicted Top1 class distribution, including T=2 fraction
- prediction entropy and Top1 margin
- each time fold delta.

Top1/Top3 and T=2 concentration are diagnostics, not substitutes for proper scores.

## Frozen bootstrap
Paired **match-date cluster bootstrap**, 5,000 replicates, seed `72012`.
Resample unique EVENT_DATE clusters with replacement; include all paired test rows from each sampled date. Report mean dLogLoss and 90% percentile interval.

## Development PASS gate
Candidate minus baseline must satisfy all:
1. pooled dLogLoss < 0;
2. paired date-cluster bootstrap 90% upper dLogLoss < 0;
3. LogLoss wins in at least 3 of 4 chronological folds;
4. pooled dBrier <= 0;
5. pooled dRPS <= 0;
6. probability conservation residual <= 1e-12;
7. 2025-26 target values read = 0;
8. C073-C077 scientific results unused;
9. C070-F Confirmation1597 unopened.

Top1/Top3 do not rescue a proper-score FAIL and do not independently veto an otherwise proper-score PASS.

## Practical breakthrough-candidate threshold
A development PASS is labeled `BREAKTHROUGH_CANDIDATE` only if pooled dLogLoss is also <= `-0.003000`.

If `-0.003 < dLogLoss < 0` while all statistical gates pass, classify only as `STABLE_SMALL_INCREMENT`, not a breakthrough.

This practical threshold is frozen before N12 target access to prevent a tiny statistically favorable result being marketed as the requested breakthrough.

## Frozen future one-shot 2025-26 confirmation policy
N12 itself MUST NOT read 2025-26 targets.

Only if N12 reaches `BREAKTHROUGH_CANDIDATE` may a separate C072-N13 execution contract open the unchanged 2025-26 target pool. N13 may not change:
- quote transform;
- eligibility;
- feature sets;
- estimator or C;
- target classes;
- metric definitions.

For N13, refit baseline and candidate once on all eligible 2020-21..2024-25 development rows, then score the untouched eligible 2025-26 rows once.

Frozen N13 confirmation gates are:
1. confirmation dLogLoss < 0;
2. confirmation dBrier <= 0;
3. confirmation dRPS <= 0;
4. paired EVENT_DATE-cluster bootstrap 90% upper dLogLoss < 0;
5. probability residual <= 1e-12;
6. no post-development tuning/reselection;
7. global-consumption audit remains clean for the opened confirmation identities.

Only N12 `BREAKTHROUGH_CANDIDATE` + N13 full confirmation PASS may be described as a **confirmed football3 dynamic-surface breakthrough**.

If N12 fails any development gate, terminal is PARK and 2025-26 remains target-unread. Do not tune C, line subset, timepoint subset, transform, folds or nonlinear model on the viewed development labels.
