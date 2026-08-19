# C072-N8 — Footiqo five-league multi-line Historical Odds bulk freeze (zero-label)

## Lineage
- football3 only.
- Parent C072-N7R1 terminal: `C072N7_FIVELEAGUE_METADATA_PASS`.
- N7R1 established five clean 22-field Historical Odds schemas and filtered row counts: EPL 4180, LaLiga 4180, Bundesliga 3059, Serie A 3799, Ligue 1 3550; pooled 18,768.
- No football row values, target values or nonce values were persisted before N8.
- C073-C077 remain quarantined.

## Objective
Freeze the actual five-league Historical Odds dataset needed for a later multi-line P(T) research contract, while reading **zero football score/result labels**.

Allowed row fields are exactly the Historical Odds schema:
`id, matchDate, Country, League, Season, homeTeam, awayTeam, H, D, A, O05, U05, O15, U15, O25, U25, O35, U35, O45, U45, BTTSY, BTTSN`.
A derived `sourceCode` identifying the fixed page (EPL/LL/BL/SA/L1) may be prepended in the artifact.

No score/result/goal-total field exists in the selected table schema and none may be requested from any other table/page.

## Fixed pages and resolver
Use the same five pages and mechanical Last-seasons table resolver frozen in N7:
- EPL, LaLiga, Bundesliga, Serie A, Ligue 1.
For each page:
1. GET page once;
2. slice at Historical Odds heading;
3. resolve exactly one Last-seasons O/U table via historical Season cells;
4. require the table ID to match N7's frozen mapping (545/555/565/575/585);
5. require unique non-empty hidden input `wdtNonceFrontendServerSide_<table_id>`.

## Bounded pagination algorithm
- page size `L=500` fixed before retrieval;
- first table-data request for each league: `start=0,length=500` using frozen N6/N7 browser-equivalent body + runtime `wdtNonce`;
- require valid JSON and read current `recordsFiltered` metadata;
- require `500 <= recordsFiltered <= 6000` for every league before continuing;
- require first page returned row count exactly `min(500, recordsFiltered)`; otherwise stop that league with pagination-structure failure;
- remaining starts are mechanically `500,1000,...` while `start < recordsFiltered`;
- no retry of a failed page and no alternate page size;
- hard maximum total table-data requests across all five leagues = 60.

This deterministic use of current `recordsFiltered` is part of the preregistered pagination algorithm, not adaptive model/data selection.

## Runtime nonce restrictions
Nonce values are memory-only and must never be printed/persisted/hashed/logged. Only nonce element presence/uniqueness/nonempty booleans may be stored.

## Row retention / artifact
For every successful page response:
- require each returned row to be an array of length 22;
- map positions mechanically to the fixed visible headers;
- retain allowed identity/odds values only;
- prepend derived `sourceCode`;
- never request or join a score/result table in N8.

After retrieval:
- concatenate all five leagues;
- sort deterministically by `sourceCode, matchDate, id, League, Season, homeTeam, awayTeam`;
- write `football-data/research/c072n8_multiline_odds.csv`;
- compute SHA-256 of the exact UTF-8 CSV bytes;
- compute ordered identity SHA-256 from `sourceCode|id|matchDate|League|Season|homeTeam|awayTeam` lines.

Raw odds/identity values MAY be present in this N8 artifact because they are the intended zero-label research data asset. Football score/result values remain forbidden.

## Frozen audit calculations
Parse decimal prices strictly finite and >1.
For each O/U line L in {0.5,1.5,2.5,3.5,4.5}, calculate two-sided valid coverage.
For valid pairs calculate de-vig `P(Over L)`.
For rows with all five valid lines test monotonicity:
`P(O0.5) >= P(O1.5) >= P(O2.5) >= P(O3.5) >= P(O4.5)`.

Also audit:
- unique/duplicate identity counts using `(sourceCode,id,League,Season)`;
- valid `matchDate` fraction;
- nonempty team/country/league/season identity fraction;
- season count and min/max observed season labels;
- per-league row counts versus live `recordsFiltered` metadata;
- forbidden score/result column names = 0.

## Frozen PASS gate
Terminal `C072N8_MULTILINE_ODDS_ZERO_LABEL_PASS` only if ALL:
1. all five pages/tables/nonces resolve without protocol drift;
2. no more than 60 table-data requests and no retries;
3. every request HTTP 2xx valid JSON with array rows of length 22;
4. for every league, retrieved row count exactly equals its first-response `recordsFiltered`;
5. pooled retained rows >= 15,000;
6. each league retained rows >= 2,500;
7. >=5 distinct seasons pooled;
8. valid date fraction >=99.5%;
9. complete identity fraction >=99.5%;
10. duplicate `(sourceCode,id,League,Season)` rate <=0.5%;
11. O/U2.5 valid two-sided coverage >=90%;
12. joint valid O/U1.5+2.5+3.5 coverage >=85%;
13. all-five-line valid coverage >=75%;
14. among all-five-line valid rows, de-vig monotonicity >=97%;
15. dataset SHA and ordered identity SHA are nonempty;
16. target/result columns requested/materialized = 0;
17. model_fit=0 and model_score=0;
18. nonce values persisted/logged/hashed = 0.

If access/protocol fails: `C072N8_ACCESS_OR_PROTOCOL_STOP`.
If pagination/coverage/data-quality gates fail: `C072N8_ZERO_LABEL_DATA_QUALITY_FAIL`.
Do not lower gates after retrieval.

## If PASS
Freeze C072-N9 before accessing any outcome labels for these identities. N9 must decide the P(T) representation and chronological development/confirmation label split **before labels open**. Preferred scientific question: whether multi-line cross-threshold total-goal shape resolves the L2 T=2 Top1 concentration beyond the already-confirmed single-line C072-F2 component, with proper scores primary and Top1-concentration diagnostics secondary.

## Hard boundaries
- No score/result label in N8.
- No model training/scoring.
- No manual Draw/0-0/1-1 boost.
- No K2/L2 consumed outcome labels used for model choice.
- C070-F Confirmation1597 sealed.
- protected assets sealed.
- C073-C077 quarantined.
- formal_weight=0; no CURRENT change.
