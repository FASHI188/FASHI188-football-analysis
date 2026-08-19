# C072-N17 — closing 1X2 composition increment for P(T)

## Project and evidence class

- project: `football3`
- parent branch: `football3/c072n16r1-new2000-footiqo-protocol-correction-20260819`
- parent HEAD: `271578d8f9e33a5350b8b8c36bf03423a64de41d`
- N16R1 selected CSV SHA256: `b5c988c77f7f0855481297eb5878e52742a94145bc35499f29c8ac893a596997`
- N16R1 ordered identity SHA256: `65491bb169bc1257ac802970a9e235324b55085863ba53fdf6c84a74b275a559`
- N16R1 artifact: `9368768296`
- classification: **DEVELOPMENT / REPLICATION, NOT CONFIRMATION**.
- C073-C077 scientific conclusions remain quarantined. Cross-project history is used only to classify global target consumption and cannot choose N17 model/features/gates.

The exact scientific question is frozen before any N17 target decode:

> Does the closing de-vigged 1X2 market composition add match-level exact-total information beyond the already-available five-line closing O/U surface?

This is not a new O/U representation search. It is one orthogonal current-match market-state increment. Because 1X2/score-market ideas have appeared elsewhere in the shared research history and parts of the underlying result domain are globally consumed, N17 may never be described as pristine, blind or independent confirmation.

## Frozen N16R1 split

Input is exactly 2,000 N16R1 identities.

Development target decode is authorized only for these source-season cells:

- Brazil Serie A (`BR`): 2015 through 2024.
- USA MLS (`MLS`): 2015 through 2024.
- Greece Super League (`GR`): 2018/2019 through 2023/2024.
- Turkey Super Lig (`TR`): 2015/2016 through 2023/2024.

This deterministic split must reproduce exactly:

- development: **1,734** identities = BR 523, GR 203, MLS 594, TR 414.
- later reserve: **266** identities.

The 266 later-reserve target values remain unread. No development shortfall may be repaired by pulling a reserve identity.

## Target and source projection

Target is fixed as `T = min(FTHG + FTAG, 7)`, classes `0,1,2,3,4,5,6,7+`.

Footiqo historical Overview is the target join source. The downloader may request only the frozen development seasons. The server-side response can transport rows from the requested development season, but the program must decode/materialize `FTHG` and `FTAG` **only when the row ID belongs to the frozen 1,734 development identities**. For non-selected rows, result cells are not normalized, stored, hashed, scored or written. `FTR`, first-half, second-half, scoreline, corners/cards and other target/stat fields are not decoded for N17.

Any returned row whose `Season` differs from the requested development season is a fail-closed STOP. Any duplicate sourceCode+id, identity mismatch, target join coverage below 99%, or any later-reserve target decode is a STOP before model fitting.

## Frozen features

### Baseline B

- five closing O/U pairs: 0.5, 1.5, 2.5, 3.5, 4.5.
- each pair is de-vigged as `p_over = (1/O)/(1/O + 1/U)` and represented by `logit(p_over)`.
- source league one-hot.
- median imputer + StandardScaler for numeric features.
- multinomial LogisticRegression, `C=0.1`, `lbfgs`, no class weights.

### Candidate C

Exactly B plus two de-vigged closing 1X2 compositional coordinates. With normalized inverse-odds probabilities `pH,pD,pA`:

1. `hda_gap = log(pH/pA)`
2. `hda_draw = log(pD/sqrt(pH*pA))`

These two coordinates are frozen as a lossless two-dimensional representation of the three-part 1X2 composition. No BTTS feature, no 1X2 overround feature, no interaction, no score-history feature, no movement feature, no feature subset search, no nonlinear model and no alternate C are allowed.

## Frozen rolling OOS

Five expanding calendar-year folds:

- train <=2019, test 2020
- train <=2020, test 2021
- train <=2021, test 2022
- train <=2022, test 2023
- train <=2023, test 2024

All rows are paired B vs C. No random train/test split.

## Metrics and gates

Primary metric is exact-T multiclass LogLoss. Also report multiclass Brier, normalized RPS, Top1 and Top3. Top1/Top3 are diagnostics and cannot override a proper-score failure.

Paired match bootstrap is frozen at 3,000 resamples, seed `72017`, 90% interval for candidate-minus-baseline LogLoss.

N17 development PASS requires all:

1. pooled dLogLoss < 0;
2. paired bootstrap 90% upper bound < 0;
3. dBrier <= 0;
4. dRPS <= 0;
5. LogLoss improves in at least 4/5 chronological folds;
6. LogLoss improves in at least 3/4 source leagues;
7. probability conservation residual <= 1e-10;
8. later-reserve target values decoded = 0.

Failure of any required scientific gate => `PARK`. No post-view repair is authorized on N17 labels.

## Hard stopping and isolation

After N17 target labels are viewed, prohibited actions include changing C, adding/removing 1X2 coordinates, adding BTTS/AH/score-history/movement, changing folds, changing league subsets, changing imputation/scaling, changing PASS gates or selecting a similar model on the same OOS labels.

C070-F Confirmation1597 remains sealed. A-League men/women 2025/26 reserves remain sealed. No C073-C077 scientific result may be imported to interpret model choice or stopping rules. `formal_weight=0` regardless of outcome.