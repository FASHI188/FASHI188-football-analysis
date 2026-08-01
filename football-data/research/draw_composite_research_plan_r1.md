# Composite PIT draw-signal research plan R1.1

Status: `FROZEN_PLAN_NOT_EXECUTED_GAPS_CLOSED`

Base main: `605abf2d9f98c46f063106c7bd47193b96e588e4`

## Inventory result

The bound V5.0.3 ledger contains 42 canonical routes: 14 `UNRESOLVED`, four with missing result evidence, six with candidate-improvement flags, 18 PIT-blocked, seven previously rejected, two duplicates and one audit-only route. The complete route-by-route decision is stored in `draw_composite_route_inventory_r1.json`.

The raw-field ledger covers all 176 fields. It excludes 139 retrospective market fields because original quote timestamps are unproved, excludes direct use of all 18 post-match fields, excludes `Referee` because availability timing is unproved, and limits `round` to a KOR-only secondary ablation. Selected result and shot fields may only feed strictly lagged derived features when every source match precedes the current kickoff. The complete field list is stored in `draw_composite_raw_field_pit_ledger_r1.json`.

## Excluded routes

Market, AH, OU, multibook and movement routes are excluded for unproved PIT timing. Lineup, expected-XI, player-value and referee routes are excluded for incomplete PIT or execution evidence. Previously rejected routes are not reopened. Registry routes are duplicates. Missing-result routes remain excluded. H/A risk-veto routes are not draw-probability predictors. `round` failed its frozen single-field research gate and is not part of the core.

## Researchable families

- S1 strength closeness;
- S2 strictly lagged recent form and attack/defence differences;
- S3 historical draw propensity;
- S4 low-goal environment;
- S5 exact rolling-OOF baseline probability uncertainty;
- S6 competition and verified-stage interactions.

Secondary only:

- S7 lagged shot quality on a coverage-matched subset;
- S8 KOR-only round interaction.

## Unique challenger

`C5_DRAW_COMPOSITE_PIT_R1_CORE` is the only recommended challenger. It is a deterministic fixed-L2 draw-residual logistic adjustment using S1-S6. It adjusts the draw logit while preserving the baseline H/A ratio. There is no random split, no hyperparameter search and no target-season refit.

## R1.1 execution contract

`draw_composite_execution_contract_r1.json` freezes the executable interpretation of this plan:

- exact base tree and critical source-asset identities;
- all 17 PIT dataset SHA-256 values and the five complete seasons used per competition;
- exclusion of partial 2026 calendar-year seasons;
- 51 expected rolling-origin outer folds;
- global candidate training cutoff strictly before each target season starts;
- date-only conservative PIT rule and same-date batch prediction before updates;
- common core cohort and coverage-matched X1/X2 cohorts;
- exact formulas for S1-S8;
- outer-training-only imputation, scaling and categorical encoding;
- deterministic duplicate removal and no label-based feature selection;
- fixed IRLS/Newton solver, convergence and probability-conservation gates;
- fixed metrics, ECE bins, calibration definitions, aggregation and bootstrap;
- coverage, stability, calibration and numerical support gates;
- a separate one-run authorization file that must not exist before later user authorization.

The fail-closed validator is `validate_draw_composite_prereg_r1.py`. Its exact-HEAD GitHub workflow may hash complete files and read CSV headers only; it must parse zero data rows and zero labels.

## Frozen comparison

The single later comprehensive run, if separately authorized, must report:

- B0 current exact rolling-OOF baseline;
- A1-A6 single families;
- C1-C5 fixed combinations;
- C5-minus-S1 through C5-minus-S6;
- X1 core plus lagged shots on an identical coverage-matched subset;
- X2 KOR core plus round on an identical KOR subset.

All candidates and exclusions must be reported. Cherry-picking is prohibited.

## Validation and metrics

Only complete-season rolling origin is allowed. Random splitting is prohibited. Every target row is `VIEWED_RESEARCH_EVALUATION_NOT_BLIND`.

Required metrics:

- Accuracy;
- Macro-F1;
- Draw Precision, Recall and F1;
- Log Loss;
- Brier;
- RPS;
- draw ECE and top-label multinomial ECE;
- draw calibration intercept and slope;
- fixed-bin reliability;
- pooled, equal-fold and equal-league summaries;
- per-fold and per-league stability;
- paired outer-fold bootstrap intervals.

## Holdout decision

- 2025 / 2025-26: `VIEWED_NOT_BLIND`;
- existing completed result data: `NO_PROVABLY_UNTOUCHED_EXISTING_RESULT_SET`;
- partial 2026 seasons: excluded.

A later passing run can only yield `RESEARCH_SUPPORT_ONLY_NO_PROMOTION`. A failing run yields `RESEARCH_NO_SUPPORT_NO_PROMOTION`. Neither permits formal promotion because there is no genuinely untouched holdout.

## Authorization boundary

Current state remains preregistration only:

- experiment executed: 0;
- label rows read by preregistration preflight: 0;
- Provider/API/Secret access: 0;
- new data collection: 0;
- formal model/data/config/CURRENT changes: 0;
- `formal_weight=0`;
- Ready and merge unauthorized.

The next experiment requires separate user authorization and a bound authorization file. Maximum comprehensive runs: one.
