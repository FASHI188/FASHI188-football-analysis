# C072-N16 — New Footiqo 2000-match zero-label download

## Lineage / scope
- Project: football3 only.
- Parent HEAD: `9128dcc0f5230b1a228a0b90ef234b804785767d` (C072-N15W post-run PARK adjudication).
- Purpose: create an immutable **exactly 2,000-match** zero-label pre-match market asset from competition domains not used by football3 N8.
- This stage reads no result/score/goal target, fits no model and scores no model.
- C073-C077 scientific conclusions remain quarantined; C070-F Confirmation1597 and all sealed A-League 2025/26 reserves remain unopened.

## Pre-download global-consumption audit
Before this contract was frozen, repository and shared Airtable maintenance-history searches returned zero matches for the following exact source/domain combinations:
- `Turkey Super Lig Footiqo`
- `Greece Super League Footiqo`
- `Brazil Serie A Footiqo`
- `USA MLS Footiqo`

This absence of discovered use authorizes zero-label acquisition only; it is not a pristine-confirmation claim.

## Fixed source pages
Footiqo public league pages, in this exact source order:
1. `https://footiqo.com/database/leagues/turkey-super-lig/`
2. `https://footiqo.com/database/leagues/greece-super-league/`
3. `https://footiqo.com/database/leagues/brazil-serie-a/`
4. `https://footiqo.com/database/leagues/usa-mls/`

Expected odds table heading:
`Historical Odds: 1X2, Over/Under Goals, BTTS`

Expected odds columns exactly:
`id,matchDate,Country,League,Season,homeTeam,awayTeam,H,D,A,O05,U05,O15,U15,O25,U25,O35,U35,O45,U45,BTTSY,BTTSN`

No results/stats table may be requested.

## Fixed AJAX protocol
- page GET only to discover the server-side odds-table ID and runtime nonce;
- endpoint: `https://footiqo.com/wp-admin/admin-ajax.php`;
- action: `get_wdtable`;
- DataTables page size: 500;
- hidden runtime nonce element: `wdtNonceFrontendServerSide_{table_id}`;
- request nonce field: `wdtNonce`;
- nonce values are memory-only and must never be printed, written, persisted or uploaded.

Each source page's full filtered historical odds inventory is retrieved before selection. Hard limit: <=80 table-data POST requests pooled over all four sources.

## Zero-label columns and forbidden data
The retained source rows contain only the 22 expected identity/market columns plus `sourceCode` and an internally derived `identity_sha256`.

Forbidden to request/materialize/inspect in N16:
- FTHG, FTAG, FTR;
- half-time or second-half scores/results;
- exact score;
- total-goal target;
- any result, settlement or post-match statistic.

## Exact 2,000 selection rule — frozen before retrieval
After full retrieval:
1. Normalize HTML/text whitespace only; do not alter numeric market values.
2. Define identity string exactly as:
   `sourceCode|id|matchDate|Country|League|Season|homeTeam|awayTeam`.
3. `identity_sha256 = SHA256(UTF8(identity_string))`.
4. Exact duplicate identity strings are invalidated if their 22-column row contents conflict; byte-equivalent duplicate rows are deduplicated.
5. Pool all non-conflicting unique rows from the four sources.
6. Sort by `(identity_sha256, sourceCode, id)` ascending.
7. Retain the first **exactly 2,000** rows.

The selection does not inspect any outcome or target and cannot be changed after retrieval.

## Required outputs
- `c072n16_footiqo_new2000_zero_label.csv` — exactly 2,000 selected rows;
- `c072n16_footiqo_new2000_zero_label_summary.json` — source counts, hashes, coverage, boundary guards;
- optionally the full zero-label inventory CSV may be uploaded as an Actions artifact for audit but is not the scientific 2,000-match package.

## PASS gate
Terminal `C072N16_FOOTIQO_NEW2000_ZERO_LABEL_DOWNLOAD_PASS` requires ALL:
1. all four page GETs succeed;
2. exactly one historical odds table with exact 22-column schema resolves on each page;
3. all four full server-side inventories retrieve without pagination drift;
4. pooled non-conflicting unique rows >=2,000;
5. selected rows exactly 2,000 and selected identity hashes unique;
6. complete core identity fields =100%;
7. valid two-sided O/U2.5 price coverage >=90%;
8. joint valid O/U1.5/2.5/3.5 coverage >=80%;
9. all-five valid O/U coverage >=65%;
10. target/result columns requested/materialized =0;
11. model_fit=0, model_score=0;
12. nonce persisted/logged=0;
13. C070-F/protected/A-League reserves remain sealed; C073-C077 scientific results unused.

Any failure terminal-stops N16. Do not substitute leagues, lower coverage gates, alter hash selection or rerun under a different selection rule and call it N16 PASS.

## Downstream status
N16 PASS means only: an exact, hash-bound, zero-label 2,000-match asset exists. It does not authorize target acquisition or scientific scoring. A later experiment requires a new preregistration and another global-consumption check for the exact selected identities/seasons/hypothesis.
