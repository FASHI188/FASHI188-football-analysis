# V5.1 Context-Market Prototype R1

This Draft research branch implements fail-closed V5.1 input and audit contracts. Formal weight is 0. It does not mutate CURRENT, main, formal models, formal data or formal probabilities.

## Executable stages

1. `materialize_v510_context_feature_packets_r1.py`
   - converts every existing frozen context event into a deterministic packet;
   - preserves source timing, explicit missing semantics and synchronized 1X2/AH/OU evidence;
   - applies no fitted context coefficient.

2. `audit_v510_pit_context_readiness_r1.py`
   - aligns frozen context packets with the official result inbox;
   - counts draws including 0-0;
   - refuses context fitting until the frozen minimum-sample gate passes.

3. `evaluate_v510_market_residual_1x2_r1.py` and `evaluate_v510_direct_draw_residual_r2.py`
   - use the frozen neutral 1000 benchmark with strict date order and same-day batch updates;
   - both challengers were worse than the market baseline and remain rejected at weight 0.

4. `materialize_v510_total_goal_difference_contract_r1.py`
   - defines direct total-goals support `T=0..6,7+`;
   - defines conditional goal difference `P(D|T,X)`;
   - validates legal score mapping for exact totals 0–6;
   - blocks the score matrix when the 7+ tail is not decomposed.

5. `audit_v510_existing_score_market_pit_ledger_r1.py`
   - scans all existing `processed/**/*.csv` files without new collection;
   - separates historical score-label sufficiency from strict pre-match feature provenance;
   - produces a row-level score/market/PIT ledger and a compact status receipt.

## Latest evaluated evidence

Evaluated head: `925f30700f203bf8b2daf9571d5ec9f936f775cf`

- workflow run: `30924186587` — success
- job: `92042169317`
- artifact: `8898380979`
- artifact SHA-256: `50d22612107155fe8a2c5843d5e53d3882e91f51ee54a30af3aa2e44977a2859`

## Score-label ledger result

Status: `PASS_SCORE_LABEL_LEDGER_STRICT_PIT_FEATURES_UNAVAILABLE`

- processed CSV files: 49
- valid score+identity rows: 26,873
- competitions with complete identity: 16
- draws: 6,976
- 0-0: 1,910
- 7+ total-goal labels: 630
- rows with retrospective 1X2 reference: 25,613
- rows with retrospective synchronized 1X2/AH/OU reference: 13,108
- original market quote timestamps: 0
- explicit kickoff timezone provenance: 0
- exact frozen-context identity matches: 0
- strict PIT eligible rows: 0

The historical score archive is therefore large enough for isolated label-structure research, including 7+ tail research. It is not a strict-PIT model-training set.

## Evidence ruling

Processed closing-price fields are `回顾性市场参考`. A closing-price column name does not establish the original quote time, tradability or synchronization window. No processed row is promoted to a formal pre-match snapshot.

The UEFA Champions League 90-minute-safe file contains score rows but its current schema does not expose the complete fixture identity required by this ledger, so those rows are not silently added to the 26,873 complete-identity rows.

## Current model gate

- historical score-label coverage: PASS
- strict PIT feature coverage: FAIL CLOSED
- direct total-goals fit: not allowed
- conditional goal-difference fit: not allowed
- unified score matrix: unavailable
- current-match probabilities: unavailable
- exact score: unavailable
- EV: unavailable

Fixed outputs remain:

- `总进球分布不可用。`
- `精确比分不可用。`

The next allowed research stage is an isolated historical label-structure challenger using chronological competition-season splits for direct `T=0..7+` and conditional `D|T`. It must use no market or web-context coefficients, remain formal weight 0, and produce no current-match probability or exact-score output.
