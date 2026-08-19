# C072-N11 — A-League dynamic O/U zero-label coverage contract

Project: football3 only
Parent: `C072N11_DYNAMIC_MULTILINE_OU_ZERO_LABEL_SOURCE_CONTRACT.md`
Predecessor: A-League header-only gate `ALEAGUE_HEADER_PASS`

## Source identity
Official public Betfair Data Scientists repo:
`betfair-datascientists/betfair-datascientists.github.io`
revision `9fe7fb127cd05316dbd438fe0e5be82c5c3ed536`.

Exactly six men's A-League `All Markets` files, 2020-2021 through 2025-2026, with byte hashes frozen in `C072N11_BETFAIR_HUB_ALEAGUE_HEADER_POSTRUN.md`.

## Why this is a versioned source-specific continuation
The parent N11 preferred T-24h/T-6h/T-1h snapshots for generic timestamped sources. Before any source row or target value was decoded, the official A-League header-only gate established that this provider exports fixed prematch snapshots at exactly T-60min, T-30min and T-1min. This contract does not amend the parent retrospectively. It freezes a provider-native source feasibility test at **T-60min/T-30min/T-1min before any data-row or target access**.

Any later scientific experiment using these source-native cutoffs requires a separate preregistration before target access.

## Allowed row fields
Only these columns may be decoded from data rows:
- `EVENT_DATE`
- `PATH`
- `EVENT_ID`
- `MARKET_TYPE`
- `MARKET_ID`
- `MARKET_NAME`
- `SELECTION_ID`
- `RUNNER_NAME`
- `HANDICAP`
- `HOME_TEAM`
- `AWAY_TEAM`
- `BEST_BACK_PRICE_60_MIN_PRIOR`
- `BEST_LAY_PRICE_60_MIN_PRIOR`
- `MATCHED_VOLUME_60_MIN_PRIOR`
- `BEST_BACK_PRICE_30_MIN_PRIOR`
- `BEST_LAY_PRICE_30_MIN_PRIOR`
- `MATCHED_VOLUME_30_MIN_PRIOR`
- `BEST_BACK_PRICE_1_MIN_PRIOR`
- `BEST_LAY_PRICE_1_MIN_PRIOR`
- `MATCHED_VOLUME_1_MIN_PRIOR`

## Forbidden row fields
Their column names are known from the header, but **their values may not be indexed, decoded, aggregated, compared or emitted** in N11:
- `RUNNER_STATUS`
- `IS_WINNER`
- `TOTAL_GOALS`
- `HOME_SCORE`
- `AWAY_SCORE`
- `TOTAL_MATCHED_VOLUME`
- `LAST_PREPLAY_PRICE`

Also forbidden: any external result/score join, model fit, model score, target inference, C070-F Confirmation access, or quarantined C073-C077 scientific result use.

## Frozen full-match O/U recognition
Preferred lines are exactly 0.5, 1.5, 2.5, 3.5, 4.5.

A market is eligible only when either:
1. `MARKET_TYPE` is exactly one of `OVER_UNDER_05`, `OVER_UNDER_15`, `OVER_UNDER_25`, `OVER_UNDER_35`, `OVER_UNDER_45`; or
2. `MARKET_NAME` exactly matches case-insensitive `Over/Under N.5 Goals` for one of the preferred values.

First-half, team totals, corners, cards, match-odds combinations, exact total goals and Asian totals are excluded.

Runner identity must mechanically resolve to exactly one Over and one Under selection for the same preferred half-goal threshold. No fuzzy text matching or manual aliases.

## Frozen event identity
Primary match identity is `EVENT_ID`.
For auditing only, cross-check each EVENT_ID has a single normalized `(EVENT_DATE, HOME_TEAM, AWAY_TEAM)` tuple. Any contradictory tuple is an identity conflict and the event is excluded from coverage.

## Snapshot completeness
For each event × line × frozen snapshot T-60/T-30/T-1:
- both Over and Under runners must exist;
- each runner must have finite `BEST_BACK_PRICE_* > 1` and finite `BEST_LAY_PRICE_* > 1`;
- each runner must satisfy best-back <= best-lay;
- matched-volume fields are diagnostic only and are not required to be positive.

A line is `all3_complete` only if it is complete at all three frozen snapshots.
A match has `k preferred lines all3` when at least k of {0.5,1.5,2.5,3.5,4.5} are all3_complete.

## Zero-label outputs
Report:
- exact file bytes/SHA verification;
- decoded-row count limited to allowed fields;
- per-season unique match identities;
- eligible O/U market and line counts;
- per-line T-60/T-30/T-1 complete-match counts;
- per-line all3-complete counts;
- per-season and pooled match counts with >=2, >=3 and all five preferred lines all3;
- duplicate/identity-conflict diagnostics;
- invalid/crossed quote diagnostics;
- explicit target/outcome forbidden-value access counter = 0;
- model_fit = model_score = 0.

## Frozen source PASS gates
`ALEAGUE_DYNAMIC_OU_SOURCE_PASS` requires all of:
1. all six exact pinned source hashes match the header-only gate;
2. forbidden outcome/result values accessed = 0;
3. model_fit = model_score = 0;
4. identity conflicts = 0 after fail-closed exclusion;
5. pooled O/U2.5 all3-complete matches >= 600;
6. pooled matches with >=3 preferred lines all3 >= 450;
7. each of development seasons 2020-2021 through 2024-2025 has >=60 matches with >=3 preferred lines all3;
8. reserved 2025-2026 has >=80 matches with >=3 preferred lines all3;
9. C073-C077 scientific conclusions remain unused and C070-F Confirmation1597 remains unopened.

`ALEAGUE_DYNAMIC_OU_SOURCE_LIMITED` if genuine dynamic multi-line O/U exists but one or more scale gates fail.
`ALEAGUE_DYNAMIC_OU_SOURCE_STOP` if genuine dynamic preferred-line O/U cannot be established or PIT snapshot fields are unusable.

## Authorization boundary
Even SOURCE_PASS authorizes **no target access**. Before reading `TOTAL_GOALS/HOME_SCORE/AWAY_SCORE/IS_WINNER`, freeze a separate scientific P(T) contract with baseline, candidate, feature construction, train/OOS split, bootstrap, stopping rule and global-consumption classification.
