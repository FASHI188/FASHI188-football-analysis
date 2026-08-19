# C072-N12 — Dynamic O/U2.5 trajectory P(T) development

## Lineage / classification
- Project: football3 only.
- Parent: C072-N11R1 zero-label kickoff/PIT join PASS on football3 lineage.
- N11R1 authoritative run: `32256083111`; artifact id `9366351354`; manifest SHA256 `96eb5dd412883d903cdf2ff0ebbffb8244f1a6f552fbeea1d4a1153f3a7b7faf`.
- N11R1 accepted 1,616 unique top-five 2023/24 identities; 1,610 have complete strict T-24h/T-6h/T-1h O/U2.5 freezes; PIT violations=0; target/result values read=0.
- Dynamic source is immutable `fabul0us/football_odds_2023-24` revision `211feb35f9dcd270bd7a1b27b39a8b1f45f239aa`, `match_odds.csv` SHA256 `c0e8854302159e1a8c529463f33280b728909c5e0ba95262515a7a144a43aa2a`.
- Outcome source is immutable `nm2890/football-data` revision `279978313f9c16a210fa80e8986fa22f0f866fba`, same five top-league files used by N11R1.
- The 2023/24 top-five result domain is already globally consumed in the wider research program. Therefore N12 is **REPLICATION / REPRODUCTION** only, never fresh, blind, pristine or independent confirmation.
- C073-C077 scientific conclusions remain quarantined and are not design/evidence input. C070-F Confirmation1597 remains sealed.

## Scientific question frozen before N12 target access
Does the **shape of the pre-match O/U2.5 trajectory** add stable match-level resolution for complete `P(T=0,1,2,3,4,5,6,7+)` beyond a strong endpoint-movement representation, and does the full trajectory materially beat a near-kickoff O/U2.5 level alone?

No Draw/0-0/1-1/T=2 manual adjustment is permitted.

## Eligible rows
Use only exact N11R1 manifest rows with `complete_24h_6h_1h == 1`.

No row may be substituted, repaired, rematched or added after target access.

## Target decoding boundary
For each fixed source file, N12 may decode `FTHG` and `FTAG` **only** for physical row indices already listed in the immutable N11R1 complete manifest. Every unselected physical row must be skipped before CSV field parsing.

For an authorized row, `FTHG` and `FTAG` must both parse as finite nonnegative integers. Invalid authorized labels are excluded from every compared model with no replacement.

Target:
`T = min(FTHG + FTAG, 7)` with classes `[0,1,2,3,4,5,6,7+]`.

No C070-F/protected target is opened.

## Market probability transform
For every valid Under/Over 2.5 pair:
`q = (1/Over) / ((1/Over) + (1/Under))` = de-vig probability of Over 2.5.

For model input:
`logit(q) = log(q/(1-q))`, clipped only to `[1e-6,1-1e-6]` for numerical safety.

From the immutable N11R1 manifest define:
- `L24`: O/U2.5 logit at the frozen T-24h observation;
- `L6`: O/U2.5 logit at the frozen T-6h observation;
- `L1`: O/U2.5 logit at the frozen T-1h observation.

## Frozen full-trajectory summaries
Re-read the immutable dynamic CSV only for N11R1 complete identities. For each match:
- window is `(kickoff-24h, kickoff-1h]` plus the frozen T-24h state as the initial state;
- retain distinct source `U/O 2.5 timestamp` states in chronological order;
- all retained timestamps must be <= T-1h and < kickoff;
- no quote after T-1h may enter any N12 feature.

On the de-vig O/U2.5 logit path compute exactly:
1. `path_total_variation = sum(abs(delta logit))`;
2. `path_range = max(logit)-min(logit)`;
3. `path_log1p_updates = log1p(number of source states strictly after T-24h and <=T-1h)`;
4. `path_max_abs_step = max(abs(delta logit))`, 0 if no post-T24 change.

No path-feature subset selection or alternative window is allowed after scoring.

## Frozen compared representations
All models share league one-hot (`competition`) and the same model pipeline.

### Static baseline B0
Numeric features:
- `L1`

### Strong endpoint-movement baseline B1 — PRIMARY comparator
Numeric features:
- `L24`
- `L1`

This spans O/U2.5 level plus 24h→1h endpoint movement without hand-coding a separate delta.

### Full trajectory candidate C
Numeric features:
- `L24`
- `L6`
- `L1`
- `path_total_variation`
- `path_range`
- `path_log1p_updates`
- `path_max_abs_step`

Primary scientific comparison: `C - B1`.
Broad dynamic-component comparison: `C - B0`.
Endpoint-movement diagnostic: `B1 - B0`.

## Frozen model family
Each representation uses the identical pipeline:
- numeric median imputation;
- StandardScaler;
- competition OneHotEncoder(handle_unknown='ignore');
- multinomial LogisticRegression;
- `C=0.1`;
- `max_iter=3000`;
- `class_weight=None`;
- `random_state=0`;
- no model/hyperparameter/feature/subset search.

## Frozen chronological OOS construction
Sort eligible zero-label identities by `(kickoff, competition, home, away)`.
Let `U` be the sorted unique kickoff timestamps and `m=len(U)`.

Index boundaries are mechanically frozen as:
- `b0=floor(0.35*m)`
- `b1=floor(0.48*m)`
- `b2=floor(0.61*m)`
- `b3=floor(0.74*m)`
- `b4=floor(0.87*m)`
- `b5=m`

Five folds:
- fold1 test kickoff in `U[b0:b1]`, train kickoff strictly before `U[b0]`;
- fold2 test `U[b1:b2]`, train strictly before `U[b1]`;
- fold3 test `U[b2:b3]`, train strictly before `U[b2]`;
- fold4 test `U[b3:b4]`, train strictly before `U[b3]`;
- fold5 test `U[b4:b5]`, train strictly before `U[b4]`.

No same-kickoff group may be split across train/test. No random split.

Every training fold must contain all 8 target classes; otherwise terminal DATA_INSUFFICIENT, not a model PASS/FAIL.

## Metrics
For B0, B1 and C:
- multiclass LogLoss;
- multiclass Brier;
- RPS;
- Top1 accuracy;
- Top3 accuracy;
- mean entropy;
- Top1 margin;
- Top1 T=2 fraction.

Primary deltas are candidate minus B1.
Broad deltas are candidate minus B0.
Endpoint deltas are B1 minus B0.

Paired bootstrap on pooled OOS rows:
- 3000 reps;
- seed `72012` for C-B1;
- seed `72013` for C-B0;
- seed `72014` for B1-B0;
- report 90% CIs and P(delta<0).

Report fold and competition LogLoss deltas for all three comparisons.

## Primary full-trajectory development PASS gate
`C072N12_DYNAMIC_OU25_TRAJECTORY_DEVELOPMENT_PASS` requires ALL for C vs B1:
1. all five chronological folds execute and every training fold contains all 8 classes;
2. pooled dLogLoss < 0;
3. paired bootstrap90 upper dLogLoss < 0;
4. >=4/5 folds improve LogLoss;
5. pooled dBrier <= 0;
6. pooled dRPS <= 0;
7. pooled Top1 delta >= 0;
8. pooled Top3 delta >= 0;
9. >=4/5 competitions improve pooled LogLoss;
10. probability-conservation residual <=1e-12;
11. target boundary guards and C070-F seal hold.

## Broad dynamic-component PASS vs static
Separately report `dynamic_component_vs_static_pass` requiring the same statistical/proper-score/stability conditions for C vs B0.

Separately report `endpoint_movement_vs_static_pass` under the same conditions for B1 vs B0.

## Breakthrough screen
`breakthrough_screen_pass=true` only if:
- primary C-vs-B1 development PASS is true;
- pooled C-vs-B1 dLogLoss <= `-0.003`;
- dynamic_component_vs_static_pass is true;
- pooled C-vs-B0 dLogLoss <= `-0.005`.

This is deliberately stronger than ordinary development PASS. A breakthrough-screen PASS is still only reproduction evidence because these labels are globally consumed; a separately frozen globally unconsumed confirmation is mandatory.

## Terminal interpretation
- If breakthrough screen passes: `C072N12_DYNAMIC_OU25_TRAJECTORY_BREAKTHROUGH_SCREEN_PASS`.
- Else if primary C-vs-B1 passes: `C072N12_DYNAMIC_OU25_TRAJECTORY_DEVELOPMENT_PASS`.
- Else if endpoint movement vs static passes but primary path-shape comparison fails: `C072N12_ENDPOINT_MOVEMENT_PASS_PATH_SHAPE_NOT_ESTABLISHED`.
- Else: `C072N12_DYNAMIC_OU25_TRAJECTORY_PARK`.

No post-view repair on these labels: do not change C, folds, cutoffs, path window, path summaries, thresholds, feature subset, league subset or model family and rerun as N12.
