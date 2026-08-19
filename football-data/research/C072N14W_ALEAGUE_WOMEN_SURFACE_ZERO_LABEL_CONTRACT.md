# C072-N14W — A-League Women O/U surface zero-label source/coverage gate

## Lineage / classification
- Project: **football3** only.
- Parent football3 checkpoint: C072-N13 adjudicated `C072N13_ALEAGUE_DYNAMIC_MULTILINE_PT_PARK`, HEAD `460a092c5856f7f7b26a56c2714397b08cfb6983`.
- Scientific root remains C072-C (`e3e73c998020beef585cc459a69ea5b73b44ddb3`).
- C073-C077 and descendants remain scientifically quarantined.
- C070-F Confirmation1597 and protected assets remain sealed.
- N14W is **ZERO-LABEL SOURCE/COVERAGE AUDIT ONLY**; model_fit=0, model_score=0, formal_weight=0.

## Why this is a new data plan
N13 consumed men's A-League development labels while testing an unconstrained 15-logit multinomial representation. After that PARK, a distinct post-view hypothesis was formulated: half-goal O/U lines are cumulative tail probabilities and should be used through a structurally constrained `P(T)` reconstruction rather than as fifteen correlated free regressors.

That hypothesis may not be repaired on N13 labels. N14W therefore moves to a separate competition/domain: **A-League Women**. Before any target access, GitHub and the shared Airtable history were searched for `A-League Womens`, `A-League_Womens`, `betfair-datascientists`, and the pinned source revision; no prior consumption record was found. Absence of a record is not itself a confirmation claim; it authorizes only this zero-label audit.

## Pinned source
Repository: `betfair-datascientists/betfair-datascientists.github.io`
Revision: `9fe7fb127cd05316dbd438fe0e5be82c5c3ed536`
Directory: `docs/data/assets/`

Exact files:
- `A-League_Womens_2020-2021_All_Markets.csv`
- `A-League_Womens_2021-2022_All_Markets.csv`
- `A-League_Womens_2022-2023_All_Markets.csv`
- `A-League_Womens_2023-2024_All_Markets.csv`
- `A-League_Womens_2024-2025_All_Markets.csv`
- `A-League_Womens_2025-2026_All_Markets.csv`

N14W may download these six files only to hash them and materialize explicitly allowed non-target columns. The authoritative N14W run freezes the exact SHA256 of each file for any later experiment.

## Allowed columns only
Materialize only:
- `EVENT_DATE`, `EVENT_ID`
- `MARKET_TYPE`, `MARKET_ID`, `MARKET_NAME`
- `SELECTION_ID`, `RUNNER_NAME`
- `HOME_TEAM`, `AWAY_TEAM`
- `BEST_BACK_PRICE_60_MIN_PRIOR`, `BEST_LAY_PRICE_60_MIN_PRIOR`
- `BEST_BACK_PRICE_30_MIN_PRIOR`, `BEST_LAY_PRICE_30_MIN_PRIOR`
- `BEST_BACK_PRICE_1_MIN_PRIOR`, `BEST_LAY_PRICE_1_MIN_PRIOR`

Explicitly forbidden from materialization/inspection in N14W:
- `TOTAL_GOALS`
- `IS_WINNER`
- `HOME_SCORE`, `AWAY_SCORE`
- `RUNNER_STATUS`
- any derived result/score/goal target.

## Preferred O/U structure
Preferred full-match totals lines exactly:
`0.5, 1.5, 2.5, 3.5, 4.5`.

Recognition is fail-closed:
- `MARKET_TYPE` exactly `OVER_UNDER_05/15/25/35/45`, or exact market-name equivalent `Over/Under X.5 Goals`;
- runner names exactly `Over X.5` / `Under X.5` with optional literal `Goals` suffix;
- one market ID per event×line;
- exactly one selection ID per side;
- duplicate/conflicting market/runner observations invalidate that event×line;
- each snapshot requires finite back>1, lay>1, and back<=lay for both Over and Under.

## Zero-label coverage outputs
For each season and pooled:
- unique event identities with any preferred O/U line;
- O/U2.5 complete at T-60/T-30/T-1;
- >=3 preferred lines complete at all three snapshots;
- all five preferred lines complete at all three snapshots;
- identity conflicts, duplicate-market conflicts, crossed/invalid quote counts;
- exact file bytes and SHA256.

No probability/model score and no outcome statistic is permitted.

## Frozen PASS gate
`C072N14W_ALEAGUE_WOMEN_SURFACE_ZERO_LABEL_PASS` requires ALL:
1. all six pinned files download and hash successfully;
2. required allowed-column schema is present in every file;
3. forbidden target/result columns materialized = 0;
4. pooled unique preferred-O/U event identities >= 450;
5. pooled all-five/all-three complete matches >= 350;
6. each of 2020-2021 through 2024-2025 has >=45 all-five/all-three complete matches;
7. 2025-2026 has >=45 all-five/all-three complete matches, so a later reserve can be kept sealed;
8. pooled O/U2.5 all-three completeness among preferred-O/U events >=75%;
9. pooled all-five/all-three completeness among preferred-O/U events >=55%;
10. identity-conflict event rate <=1%;
11. model_fit=0, model_score=0;
12. C070-F/protected remain sealed and C073-C077 scientific results are unused.

If any gate fails: `C072N14W_ALEAGUE_WOMEN_SURFACE_ZERO_LABEL_STOP`. Do not lower thresholds or change the source list after seeing the audit and relabel it PASS.

## If PASS
A PASS authorizes only a new preregistration for the structurally constrained `P(T)` hypothesis. It does **not** authorize outcome access by itself.

The later contract should use 2020-2021 through 2024-2025 as development and keep 2025-2026 target-sealed for a one-shot confirmation. The exact representation and tail closure must be frozen before any development target is opened.
