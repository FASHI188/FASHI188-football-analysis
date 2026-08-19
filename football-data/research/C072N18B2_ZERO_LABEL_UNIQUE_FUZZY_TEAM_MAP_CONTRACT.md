# C072-N18B2 — Zero-label high-confidence automated team-identity resolver

## Status
- project: `football3`
- parent N18B terminal: `STOP_COVERAGE` @ `364b5560556d92e97916e6dbc56eb1d0552913a8`
- branch: `football3/c072n18b2-zero-label-unique-fuzzy-team-map-20260820`
- phase: **ZERO TARGET LABELS ONLY**
- purpose: test whether a single preregistered automated cross-provider team-name resolver can recover enough identity coverage to satisfy the unchanged N18B 550-row gate.

## Why this is a new zero-label hypothesis
N18B materialized 738 Footiqo odds-only target-window identities and no outcomes. Its frozen exact-name normalizer yielded 452 eligible rows, with 249 rows failing team mapping and 37 failing the >=8-history gate. N18B is permanently STOP_COVERAGE and will not be repaired or renamed PASS.

No target result value, model score or prediction metric has been seen. Therefore one separately preregistered identity-resolution hypothesis is permitted without contaminating the future N18-C scientific test.

## Everything scientifically relevant remains unchanged
N18B2 inherits **without change**:
- six Footiqo domestic league pages;
- target window 2024-09-18 through 2024-12-31;
- season filters;
- exact 22-column odds-only Footiqo schema;
- same six consumed FotMob historical assets/release hashes;
- source-target overlap must equal zero;
- both target teams require >=8 strictly prior usable FotMob matches;
- last 10 matches, equal weight;
- exact fixed 16 chance-state features;
- high-xG threshold 0.20;
- O/U2.5 only market anchor and identical de-vig equation;
- minimum 550 eligible targets;
- strictly chronological first400 DEVELOPMENT + last150 CONFIRMATION_SEALED;
- no target outcome access, no model fit, no target scoring;
- C070-F and all existing reserves remain sealed;
- C073-C077 scientific conclusions remain quarantined.

N18B2 does **not** extend the date window, reduce 550, lower the >=8-history gate, add leagues, change the market anchor, change any feature equation or alter the 400/150 split.

## Frozen resolver
Run the original N18B normalizer first. Exact unique normalized matches remain preferred.

For a target team name with no exact match, compare it against all uniquely identified historical FotMob team names **within the same frozen league asset family only** using `rapidfuzz.fuzz.token_set_ratio`.

Frozen preprocessing remains the N18B normalizer:
- Unicode NFKD to ASCII where possible;
- lowercase;
- `&` -> `and`;
- punctuation removed;
- whitespace collapsed;
- standalone `fc`, `cf`, `afc`, `calcio` removed;
- `utd` -> `united`;
- standalone `st` -> `saint`.

A fuzzy mapping is accepted only if ALL conditions hold:
1. target normalized string length >=4;
2. target and candidate share at least one exact normalized token;
3. the best `token_set_ratio` score is **>=90.0**;
4. the best candidate is unique;
5. best score minus second-best score is **>=10.0**;
6. the resolved source normalized team name already maps to exactly one FotMob team ID in that league family.

If no candidate passes, the team remains unmapped. No manual aliases, no exception list, no city/club dictionary, no outcome-assisted mapping and no threshold changes after this run.

## Mapping receipt
Persist, before target cohort filtering:
- target provider team name;
- normalized target name;
- resolver mode (`EXACT` or `FUZZY`);
- mapped source team ID;
- mapped source normalized name;
- best score;
- second-best score;
- margin;
- accepted/rejected status.

The receipt contains identity metadata only, never target outcomes.

## PASS / STOP
Run the unchanged N18B cohort builder with only the preregistered resolver augmentation.

`PASS_N18B2_ZERO_LABEL_TARGET_MARKET_JOIN` requires the unchanged N18B gates including **>=550 eligible rows** and exact 400/150 chronological split.

If <550, terminal `STOP_COVERAGE`. Do not lower the threshold/margin, inspect labels, add aliases, change dates, add leagues or reduce the cohort size.

Only if N18B2 passes may the first N18-C model contract be frozen. Target outcomes remain closed throughout N18B2.
