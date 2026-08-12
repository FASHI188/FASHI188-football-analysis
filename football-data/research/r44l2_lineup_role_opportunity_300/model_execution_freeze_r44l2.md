# R44L2 Model Execution Freeze

## Study identity

- study_id: `r44l2_lineup_role_opportunity_300`
- research branch: `research/r44l2-lineup-role-opportunity-300`
- research-only: `true`
- formal_weight: `0`
- historical lineup PIT eligibility: `false`
- maximum possible positive verdict: `RETROSPECTIVE_SIGNAL_PASS`

This freeze is created **before any R44L2 xG/xA label/model execution**. R44L2 is an independent follow-up to the failed R1 representation; it is not an in-place rescue or hyperparameter retune of R1.

## Pre-label gates already passed

### Formal 300-match zero-label gate

- workflow run: `31594761068`
- executed HEAD: `d03f58ecfe6d675f6c0147b44466500d447f7536`
- terminal: `PASS_R44L2_ZERO_LABEL_GATE`
- 300/300 selected fixtures matched
- 600/600 team-match rows
- every team-match has exactly 11 unique starters
- every starter has a non-empty match-specific position
- every team-match has a non-empty formation
- no missing/ambiguous identities
- no conflicting starter duplicates
- artifact ID: `9140567749`
- artifact SHA-256: `c3b1082eff063589b7ba455cf101f7cd1a9057fcd1a0d4a742159ec9eeaf1c2c`

The earlier zero-label failure was preserved as engineering evidence. Its only discovered defect was the pre-label identity alias omission `Spurs -> Tottenham`; Z2 changed exactly that alias and no sample or scientific gate.

### Full-season label-free training-support gate

- workflow run: `31595059732`
- executed HEAD: `952637f26de5317bb7537faeb3986c203048f2e3`
- terminal: `PASS_R44L2_TRAINING_SUPPORT_380`
- 380/380 EPL fixtures matched
- 760/760 team-match rows usable
- 0 bad team-match rows
- 0 missing/ambiguous identities
- 0 conflicting starter duplicates
- artifact ID: `9140682873`
- artifact SHA-256: `2b8a8a768b2d92f692105135a40f9b2c10b4c70a71b38126317bd4882f2ce176`

## Frozen source identity

- FPL source commit: `8c97b2adb123863c3dd581e730f1360e89815ac2`
- Transfermarkt schema commit: `154367dfa6d6eb0b86332e332f9df0a080c7ddce`
- FPL fixtures SHA-256: `2d7e3950d346df14ca486cb09e9b9ba406d37d943775244eed06cdc021ffb3a9`
- FPL teams SHA-256: `b29df099cb0ad25413e284e53116099b0e0496874f99743dbc0870d8241b46c5`
- Transfermarkt games SHA-256: `585f593b2add005ad803fd999355ef70d5de44ef021d37787064fdffcb3ba484`
- Transfermarkt game_lineups SHA-256: `6b2fc04ae307390c4d2044659b91c0da314c6a20eefd4a2f13e1468ac06c874b`
- FPL merged_gw expected SHA-256: `0d09f1f1cb1b5520ec8e2f25238aa652efe2a263d8ca7cb2b6538b27bf86727d`

The model stage must fail closed if any source hash differs.

## Frozen sample and chronology

- league: English Premier League 2025-26
- formal sample: the final 300 fixtures ordered by `(kickoff_time, fixture_id)`
- formal window already observed by zero-label gate: `2025-10-24T19:00:00Z` through `2026-05-24T15:00:00Z`
- pre-formal support: the first 80 fixtures of the same season
- all 300 formal fixtures remain OOS tests; there is no reduced-sample fallback
- three sequential formal folds: matches 1-100, 101-200, 201-300
- each fold trains only on model-eligible team rows with kickoff strictly earlier than that fold's first test kickoff
- all fixtures sharing the same kickoff timestamp are featurized before any state update from that kickoff batch

## Frozen baseline B0

Exactly the R1 baseline:

1. `home`
2. `team_roll5_xg`
3. `opp_roll5_xgc`
4. `team_exp_xg`
5. `opp_exp_xgc`

All historical xG/xGC baseline features use only matches strictly earlier than the prediction fixture.

## Frozen R44L2 structural candidate features

Candidate L2 = B0 plus the following 21 predeclared Transfermarkt lineup/formation features.

### Match-specific starter role counts (12)

1. `tm_centre_back_count`
2. `tm_left_back_count`
3. `tm_right_back_count`
4. `tm_defensive_midfield_count`
5. `tm_central_midfield_count`
6. `tm_attacking_midfield_count`
7. `tm_left_midfield_count`
8. `tm_right_midfield_count`
9. `tm_left_winger_count`
10. `tm_right_winger_count`
11. `tm_centre_forward_count`
12. `tm_second_striker_count`

`Goalkeeper` is integrity-checked but excluded as a feature because it is structurally constant at one.

### Parsed formation geometry (5)

13. `formation_line_count`
14. `formation_line_1`
15. `formation_line_2`
16. `formation_line_3`
17. `formation_line_4`

Formation geometry is obtained by extracting the integer line sizes from the frozen Transfermarkt formation string in order; missing fourth line is padded with zero. Text suffixes such as `Attacking`, `flat`, `double 6`, and `Diamond` are not separately one-hot encoded. This prevents post-label category selection and limits dimensionality.

### Lineup/structure continuity (4)

18. `prev_xi_overlap`: count of current Transfermarkt starter player_ids also starting the team's immediately previous match.
19. `recent5_top11_overlap`: overlap with the 11 player_ids having the highest start frequency across the team's previous five matches, ties broken deterministically by player_id.
20. `formation_changed`: 1 if the exact current formation string differs from the immediately previous formation, else 0; first observed team match uses 0.
21. `role_l1_change`: sum of absolute differences between the current 12-role count vector and the immediately previous match's 12-role vector; first observed team match uses 0.

No FPL `GK/DEF/MID/FWD` shell feature from R1 is included in L2. No set-piece duty, LCB/RCB, manually inferred tactical duty, player market value, current-match performance field, or post-match event is allowed.

## Frozen targets

- primary: team xG, defined as sum of FPL player `expected_goals` for that team-match
- secondary: team xA, defined as sum of FPL player `expected_assists` for that team-match

Current-match xG/xA are labels only and may not enter any predictor.

## Frozen algorithm

For B0 and L2 independently:

- numeric preprocessing: `StandardScaler`, fit on fold training rows only
- model: `Ridge(alpha=10.0, fit_intercept=True)`
- target transform: `log1p(target)`
- predicted point value: `max(0, expm1(predicted_log_target))`
- Gaussian NLL sigma: standard deviation of transformed training target only, minimum 0.05, matching R1
- no hyperparameter search
- no feature deletion/addition after labels open
- no fold redefinition
- no team filtering

## Frozen evaluation

For both xG and xA:

- pooled Gaussian NLL
- RMSE
- MAE
- linear calibration intercept and slope
- per-fold NLL and delta

Primary comparison is `delta_nll_xg = L2 - B0`; negative is better.

Bootstrap:

- 10,000 match-cluster resamples
- cluster unit: fixture, averaging the two team-row xG delta NLL values within fixture first
- seed: `20260812`
- report mean and central 90% interval (`p05`, `p95`)

Robustness:

- leave-one-team-out pooled xG delta NLL for every EPL team
- improvement-contribution concentration by team; no single team may account for more than 50% of total positive improvement contribution

## Frozen research gate

R44L2 receives `RETROSPECTIVE_SIGNAL_PASS` only if all of the following hold:

1. pooled xG delta NLL < 0;
2. bootstrap 90% upper bound (`p95`) < 0;
3. at least 2 of 3 folds have xG delta NLL < 0;
4. L2 xG RMSE is not more than 2% worse than B0;
5. L2 xG MAE is not more than 2% worse than B0;
6. L2 xA RMSE is not more than 2% worse than B0;
7. L2 xA MAE is not more than 2% worse than B0;
8. every leave-one-team-out pooled xG delta NLL remains < 0;
9. maximum single-team share of total positive improvement contribution is < 50%.

Otherwise terminal verdict is `RETROSPECTIVE_SIGNAL_FAIL`.

Regardless of outcome:

- `formal_weight=0` remains fixed;
- historical lineup `available_at` is unverified, therefore `pit_eligible=false`;
- workflow success is not scientific pass;
- no result may be promoted into CURRENT or formal prediction logic from this experiment alone.
