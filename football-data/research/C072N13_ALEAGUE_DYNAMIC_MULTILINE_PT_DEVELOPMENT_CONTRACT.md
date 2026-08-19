# C072-N13 — A-League dynamic multi-line O/U P(T) development

## Lineage / scientific isolation
- Project: **football3** only.
- Scientific root: C072-C, root HEAD `e3e73c998020beef585cc459a69ea5b73b44ddb3`.
- Immediate parent: C072-N11 zero-label source branch, HEAD at branch creation `40998a55e3cc252ebc8769d6d279ec290345764a`.
- N10 static closing five-line P(T) is PARK. N12 fabul0us single-line sparse-trajectory P(T) is PARK and may not be post-view repaired.
- C073-C077 and descendants remain scientifically quarantined and are not used as evidence, design guidance, model selection, feature selection, stopping-rule input or interpretation.
- C070-F Confirmation1597 and protected assets remain sealed.
- formal_weight=0; no CURRENT promotion.

## Source / zero-label evidence already established
Pinned public source repository:
`betfair-datascientists/betfair-datascientists.github.io`

Pinned source revision:
`9fe7fb127cd05316dbd438fe0e5be82c5c3ed536`

Authoritative football3 N11 source audit:
- run `32251618515`
- job `96063712069`
- artifact `football3-c072n11-aleague-dynamic-ou-zero-label`
- artifact id `9364629795`
- terminal `ALEAGUE_DYNAMIC_OU_SOURCE_PASS`

The source audit established, with zero outcome access and zero model fitting:
- 992 unique non-conflict A-League match identities with preferred O/U markets;
- 962 matches with O/U2.5 complete at T-60/T-30/T-1;
- 979 matches with >=3 preferred lines complete at all three snapshots;
- 944 matches with all five preferred lines complete at all three snapshots;
- all-five all-three counts by season: 2020-21=125, 2021-22=156, 2022-23=159, 2023-24=168, 2024-25=174, 2025-26=162;
- target/outcome values materialized=0;
- C073-C077 scientific results used=false;
- C070-F Confirmation1597 opened=false.

## Global-consumption classification before target access
The shared-history consumption audit attached to N11 found no prior experiment on these exact Betfair Data Scientists A-League All Markets files and no prior `A-League_2025-2026` experiment. A registry reference to ten 2024/25 SkillCorner A-League sample matches exists, but no PR experiment consuming those samples was found.

Therefore N13 is classified as **DEVELOPMENT EVIDENCE, NOT CONFIRMATION**. It must not be called fresh confirmation, independent confirmation, blind confirmation or pristine confirmation. Once N13 reads development targets, those exact development targets become globally consumed.

2025-2026 remains strictly target-sealed in N13. N13 must not decode, index, aggregate, compare or materialize any 2025-2026 outcome field value.

## Scientific question frozen BEFORE target access
Does the **dynamic multi-line O/U probability surface** across lines 0.5/1.5/2.5/3.5/4.5 at provider-native T-60min/T-30min/T-1min improve complete match-level `P(T=0,1,2,3,4,5,6,7+)` beyond a strong model that already contains the dynamic O/U2.5 trajectory?

This isolates the incremental information in cross-line surface dynamics rather than merely re-proving single-line market movement.

## Fixed source files and development/reserve split
Exact files under `docs/data/assets/`:
- development: `A-League_2020-2021_All_Markets.csv`
- development: `A-League_2021-2022_All_Markets.csv`
- development: `A-League_2022-2023_All_Markets.csv`
- development: `A-League_2023-2024_All_Markets.csv`
- development: `A-League_2024-2025_All_Markets.csv`
- sealed reserve: `A-League_2025-2026_All_Markets.csv`

Frozen SHA256 values:
- 2020-2021: `e794f97ce8d95676a0cf14a78057aba8837973459eda2a1e04194402c4bfaa37`
- 2021-2022: `5411a5a311a2f2e379967b585a2e54646168ca434923e4ca5389cb614d27de78`
- 2022-2023: `916724fe4cad4af6d350805f4962c94456a14edc7afcc6184b01f3fcb77fc06d`
- 2023-2024: `2e4f761f484891bec3457b4c52a3d1ee20e5379fe5efa69e6564133edcbec1b7`
- 2024-2025: `a62ce23b14c112ae02be470bf8e29f3568f2a40a311b2b0034be6cd8c1b53cb3`
- 2025-2026 reserve reference only: `f0980a3a37b79a5be947e4a7e3288c9f88e3cf03810b4b23cb8f8871064fc5ab`

The N13 workflow must download only the five development files. It must not download the 2025-2026 file.

## Eligible market / match definition
Preferred full-match lines are exactly:
`0.5, 1.5, 2.5, 3.5, 4.5`.

Eligible market recognition and fail-closed runner handling are identical to the already-frozen N11 A-League zero-label source contract:
- MARKET_TYPE exactly `OVER_UNDER_05/15/25/35/45`, or exact market-name equivalent;
- exactly one Over and one Under runner per event × line × market;
- no fuzzy runner names;
- duplicate line market IDs fail closed;
- identity-conflict events fail closed;
- at each snapshot both runners require finite back >1, finite lay >1, and back <= lay.

N13 uses only events where **all five preferred lines are complete at all three snapshots T-60/T-30/T-1**.

Expected zero-label all-five development counts from N11: 782 total across 2020-21 through 2024-25. This is an expectation only; N13 must revalidate the exact fixed source hashes and eligibility mechanically before target access.

## Target access boundary
Allowed target field in development files only:
- `TOTAL_GOALS`

Forbidden outcome/settlement fields in N13 model construction or diagnostics:
- `IS_WINNER`
- `HOME_SCORE`
- `AWAY_SCORE`
- `RUNNER_STATUS`
- any derived H/D/A or exact-score outcome.

After all market eligibility has been established from non-target fields, N13 may decode `TOTAL_GOALS` **only on rows belonging to already-eligible development EVENT_IDs**. All repeated `TOTAL_GOALS` values for an eligible EVENT_ID may be read solely to verify within-event consistency; exactly one target value is then materialized per event. If the repeated eligible-event values are non-identical, invalid or missing, that event is excluded as target-conflict with no replacement or repair. `TOTAL_GOALS` for non-eligible events must never be decoded.

Target:
`T = min(TOTAL_GOALS, 7)` with classes `[0,1,2,3,4,5,6,7+]`.

2025-2026 target values read/materialized must remain exactly 0.

## Frozen O/U probability transform
For each runner at each line/snapshot:
`runner_mid_implied = 0.5 * (1 / best_back + 1 / best_lay)`.

For an Over/Under pair at line L and snapshot S:
`q_over(L,S) = over_mid / (over_mid + under_mid)`.

Numerical feature:
`logit_q(L,S) = log(q/(1-q))`, clipping q only to `[1e-6, 1-1e-6]` for numerical safety.

Matched volume is excluded from N13 features and cannot be used for row selection, weighting or tie-breaking.

## Frozen compared representations
All models share the same model family and no team-name feature.

### B0 — static single-line baseline
Numeric features:
- O/U2.5 `logit_q` at T-1min only.

### B1 — strong dynamic single-line baseline — PRIMARY comparator
Numeric features:
- O/U2.5 `logit_q` at T-60min
- O/U2.5 `logit_q` at T-30min
- O/U2.5 `logit_q` at T-1min

### C — dynamic multi-line surface candidate
Numeric features: all 15 line × time logits exactly:
- lines 0.5/1.5/2.5/3.5/4.5
- snapshots T-60/T-30/T-1

No manually engineered curvature, slope, interaction, PCA, line subset, smoothing, monotonic projection, movement threshold, Draw/T2 feature or matched-volume feature is allowed.

Primary scientific comparison: `C - B1`.
Broad comparison: `C - B0`.
Diagnostic single-line dynamic comparison: `B1 - B0`.

## Frozen model family
Each representation uses the identical pipeline:
- numeric median imputation;
- StandardScaler;
- multinomial LogisticRegression;
- `C=0.1`;
- `max_iter=3000`;
- `class_weight=None`;
- `random_state=0`;
- solver `lbfgs`;
- no hyperparameter/model/feature/subset search.

No league one-hot is needed because N13 is one competition only.

## Frozen chronological rolling-OOS folds
Exactly four expanding season folds:
1. test 2021-2022; train 2020-2021
2. test 2022-2023; train 2020-2021 through 2021-2022
3. test 2023-2024; train 2020-2021 through 2022-2023
4. test 2024-2025; train 2020-2021 through 2023-2024

No random split. No future season enters training.

Each training fold must contain all eight target classes. If any training fold lacks one or more classes, terminal is `C072N13_ALEAGUE_DATA_INSUFFICIENT_ALL_CLASSES`, not scientific PASS/FAIL.

## Metrics
For B0/B1/C report pooled and per-fold:
- multiclass LogLoss;
- multiclass Brier;
- RPS;
- Top1 accuracy;
- Top3 accuracy;
- fraction Top1 total=2;
- entropy;
- top1 margin;
- probability-conservation residual.

Paired pooled bootstrap on match rows:
- 3000 reps;
- C-B1 seed `72015`;
- C-B0 seed `72016`;
- B1-B0 seed `72017`;
- report 90% CI and P(delta<0).

## Frozen comparison PASS gates
For each comparison, PASS requires all:
1. all four folds execute with nonzero test rows;
2. pooled dLogLoss < 0;
3. paired bootstrap90 upper dLogLoss < 0;
4. >=3/4 chronological folds improve LogLoss;
5. pooled dBrier <= 0;
6. pooled dRPS <= 0;
7. pooled Top1 delta >= 0;
8. pooled Top3 delta >= 0;
9. probability-conservation residual <=1e-12;
10. 2025-2026 target values read/materialized = 0;
11. C070-F Confirmation1597/protected remain sealed.

Primary development PASS is the C-vs-B1 gate.

## Breakthrough screen
`breakthrough_screen_pass=true` only if all are true:
- primary C-vs-B1 PASS;
- pooled C-vs-B1 dLogLoss <= `-0.003`;
- broad C-vs-B0 PASS;
- pooled C-vs-B0 dLogLoss <= `-0.008`.

This is a demanding development-screen threshold, not a confirmation claim.

## Terminal
- if breakthrough screen passes: `C072N13_ALEAGUE_DYNAMIC_MULTILINE_PT_BREAKTHROUGH_SCREEN_PASS`
- else if C-vs-B1 passes: `C072N13_ALEAGUE_DYNAMIC_MULTILINE_PT_DEVELOPMENT_PASS`
- else if B1-vs-B0 passes: `C072N13_ALEAGUE_SINGLELINE_DYNAMIC_PASS_MULTILINE_NOT_ESTABLISHED`
- else: `C072N13_ALEAGUE_DYNAMIC_MULTILINE_PT_PARK`
- data insufficiency: `C072N13_ALEAGUE_DATA_INSUFFICIENT_ALL_CLASSES`

## Stopping / confirmation rule
No post-view repair is allowed on N13 development labels. Do not change C, model family, line subset, snapshots, price transform, fold seasons, matched-volume inclusion, gates or feature construction and rerun as N13.

If and only if the primary C-vs-B1 development gate passes, freeze an N14 one-shot 2025-2026 confirmation contract **before** downloading/reading any 2025-2026 target value. If N13 does not primary-PASS, 2025-2026 remains sealed.
