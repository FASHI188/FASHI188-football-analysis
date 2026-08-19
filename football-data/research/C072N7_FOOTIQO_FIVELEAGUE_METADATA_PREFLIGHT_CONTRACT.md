# C072-N7 — Footiqo five-league historical-odds metadata preflight

## Lineage
- football3 only.
- Parent C072-N6 terminal: `C072N6_NONCE_PREFLIGHT_PASS`.
- N6 established valid anonymous server-side JSON for EPL Last-seasons table 545 when the page's hidden `wdtNonceFrontendServerSide_545` value is sent as body field `wdtNonce`.
- N6 reported `recordsFiltered=4180`, `recordsTotal=123997`, exactly one 22-field odds row, and zero persisted row/nonce values.
- C073-C077 remain quarantined.

## Objective
Confirm that the same public server-side transport works for all five fixed top-league **Last seasons Historical Odds** tables and that their pooled filtered history is large enough to preserve the original N2 scale gate before any bulk odds retrieval.

This stage is metadata/schema confirmation only, not bulk retrieval and not model work.

## Fixed league pages
- EPL: `https://footiqo.com/database/leagues/england-premier-league/`
- LaLiga: `https://footiqo.com/database/leagues/spain-laliga/`
- Bundesliga: `https://footiqo.com/database/leagues/germany-bundesliga/`
- Serie A: `https://footiqo.com/database/leagues/italy-serie-a/`
- Ligue 1: `https://footiqo.com/database/leagues/france-ligue-1/`

## Mechanical table resolution per page
For each page independently:
1. slice HTML at `Historical Odds: 1X2, Over/Under Goals, BTTS`;
2. find O/U tables whose headers include two-sided 1.5/2.5/3.5 lines;
3. select exactly one **Last seasons** table by visible historical Season cells (must include a non-current historical season such as 2015/2016);
4. read that table's own numeric `data-wpdatatable_id` and DOM id;
5. require a unique hidden input `wdtNonceFrontendServerSide_<table_id>` with the same id/name and non-empty runtime value.

Do not hard-code table IDs as the primary resolver. N3's observed mapping may be used only as a consistency check.

## Network/request budget
For each of the five pages:
- one page GET;
- exactly one server-side table-data POST maximum after resolution;
- POST query: `action=get_wdtable`, `table_id=<resolved id>`;
- POST body: frozen N6 DataTables `draw=1,start=0,length=1`, empty search, visible-header column descriptors, plus runtime `wdtNonce`;
- no retries or adaptive body changes.

Maximum football table-data requests in N7 = 5.

## Secret/value handling
- Runtime nonce values remain memory-only, never printed/persisted/hashed.
- Returned football row values are never persisted.
- Allowed row metadata: row container type, row count, array length or object field names only.
- Allowed response metadata: status, bytes, JSON top-level keys, `recordsTotal`, `recordsFiltered`, `draw`.
- Visible table headers and resolved IDs/DOM IDs may be persisted.

## Frozen PASS gate
Terminal `C072N7_FIVELEAGUE_METADATA_PASS` only if ALL:
1. all five pages HTTP 2xx and Last-seasons tables uniquely resolve;
2. all five unique hidden nonce inputs exist and are non-empty;
3. exactly five football table-data requests are made, one per league;
4. all five responses HTTP 2xx and valid JSON objects;
5. every response returns exactly one row;
6. every row schema has 22 fields matching the historical-odds header count and no forbidden score/result field names;
7. every league reports `recordsFiltered >= 500`;
8. at least four leagues report `recordsFiltered >= 1000`;
9. pooled sum of the five `recordsFiltered` counts >= 4,000 (preserves the original N2 pooled-history scale gate); 
10. all five report positive `recordsTotal` metadata;
11. nonce values persisted/logged/hashed = 0;
12. football row values persisted = 0;
13. target/result values materialized = 0;
14. model_fit=0 and model_score=0.

If protocol drifts on any page, stop that page without retry; terminal `C072N7_PROTOCOL_DRIFT_STOP`.
If requests are blocked, `C072N7_ACCESS_BLOCKED`.
If structure/count gates fail, `C072N7_METADATA_COVERAGE_FAIL`.
Do not lower gates after viewing results.

## If PASS
Freeze C072-N8 before bulk retrieval. N8 may retrieve only the five Historical Odds tables (identity + 1X2 + O/U0.5/1.5/2.5/3.5/4.5 + BTTS), with a bounded pagination plan based on N7 counts. It must still read zero football score/result labels and must re-audit seasons, multi-line completeness, monotonicity and duplicates before any P(T) model contract is allowed.

## Hard boundaries
- No bulk retrieval in N7.
- No model fitting/scoring.
- No K2/L2 labels used.
- No Draw/0-0/1-1 manual boost.
- C070-F Confirmation1597 sealed.
- protected assets sealed.
- C073-C077 quarantined.
- formal_weight=0; no CURRENT change.
