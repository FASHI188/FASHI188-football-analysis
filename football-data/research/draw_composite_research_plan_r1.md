# Composite PIT draw-signal research plan R1

Status: `FROZEN_PLAN_NOT_EXECUTED`

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
- S6 league and stage interactions;
- S7 lagged shot quality as a coverage-matched secondary ablation;
- S8 KOR round interaction as a KOR-only secondary ablation.

Raw bookmaker uncertainty is not included.

## Unique recommended challenger

`C5_DRAW_COMPOSITE_PIT_R1_CORE`

C5 combines S1-S6. It uses fixed interactions for closeness with draw propensity, low-goal environment and stage, plus draw propensity with low-goal environment. The frozen model is an L2=2.0 draw-residual logistic adjustment. It changes the draw logit, preserves the baseline H/A ratio, uses no randomness and permits no hyperparameter search.

## Next comparison

A single separately authorized experiment must report the current baseline, all six single-family candidates, five frozen combinations, C5 minus each family, a coverage-matched shot ablation and a KOR-only round interaction ablation. Every candidate must be reported.

Validation is rolling origin by competition and complete season. Random splits are prohibited. Each outer target season requires at least two strictly earlier complete seasons. Partial seasons are excluded. Same-time matches are predicted before any result from that timestamp updates history.

Required metrics are Accuracy, Macro-F1, Draw Precision, Draw Recall, Draw F1, Log Loss, Brier, RPS, fixed-bin ECE, calibration intercept and slope, reliability tables, per-season results, per-league results and equal-season/equal-league summaries.

## Holdout conclusion

2025 is viewed and must not be called blind. No completed existing result set can be certified as untouched because the repository has no immutable access ledger proving that status. Partial 2026 data are incomplete. Therefore the next execution can only return `RESEARCH_SUPPORT_ONLY_NO_PROMOTION` or `RESEARCH_NO_SUPPORT_NO_PROMOTION`. It cannot support formal promotion.

## Frozen boundary

This stage runs no experiment, performs no final blind test, collects no new data, uses no external provider, changes no formal model/data/config/CURRENT, keeps `formal_weight=0`, and authorizes neither Ready conversion nor merge. The next step is at most one separately authorized frozen comprehensive experiment.
