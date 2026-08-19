# C072-N6 — Footiqo browser-equivalent nonce one-row preflight

## Lineage
- football3 only.
- Parent C072-N5 terminal: `C072N5_PROTOCOL_RECONSTRUCTION_PASS`.
- N5 established without any football data request that EPL Last-seasons Historical Odds is wpDataTable 545 / DOM `table_11`, uses `get_wdtable`, requires public hidden-input nonce `wdtNonceFrontendServerSide_545`, and sends that nonce under request field `wdtNonce`.
- N4's single request omitted this nonce and returned HTTP 200 with an empty body. N6 tests **only the newly established nonce correction**; it does not alter table/action/columns/folds/data target.
- C073-C077 remain quarantined.

## Fixed page / table / endpoint
- Page: `https://footiqo.com/database/leagues/england-premier-league/`
- Historical Odds Last-seasons table: plugin ID `545`, DOM `table_11`
- AJAX endpoint: `https://footiqo.com/wp-admin/admin-ajax.php`
- Query: `action=get_wdtable&table_id=545`
- Nonce DOM element: unique hidden input with id/name `wdtNonceFrontendServerSide_545`
- Nonce request field: `wdtNonce`

## Runtime nonce handling
The evaluator may GET the EPL page and read the current hidden input's `value` **in memory only**.

Binding restrictions:
- nonce value must never be printed, logged, persisted, hashed, returned in JSON, placed in exception text, or committed;
- only booleans `nonce_element_unique`, `nonce_value_nonempty`, and `nonce_sent=true/false` may be persisted;
- if the hidden input is absent, non-unique, or empty, make zero table-data requests and stop `C072N6_PROTOCOL_DRIFT_STOP`.

## Request body
Exactly one football table-data POST maximum.

Relative to N4, keep the same frozen DataTables structure and add **only** the resolved nonce field:
- query: `action=get_wdtable`, `table_id=545`;
- body: `draw=1`, `start=0`, `length=1`, empty global search;
- `columns[i]` descriptors derived mechanically from the visible Historical Odds headers, exactly as N4;
- add body field `wdtNonce=<runtime hidden-input value>`;
- no additional guessed filter/order/range/security fields;
- `X-Requested-With: XMLHttpRequest`, same-origin Origin/Referer.

No retry with another body/table/action/nonce field after the response.

## Response handling
Never persist football row values.

Persist only:
- HTTP status/content type/bytes;
- JSON top-level key names;
- `draw`, `recordsTotal`, `recordsFiltered` or legacy total keys;
- returned data container type and row count;
- first row object field names only, or array length only;
- forbidden score/result **field-name** matches only.

Do not persist team/date/odds/score/result values.

## Frozen PASS gate
Terminal `C072N6_NONCE_PREFLIGHT_PASS` only if ALL:
1. EPL page HTTP 2xx;
2. exact unique hidden input `wdtNonceFrontendServerSide_545` has a non-empty value;
3. exactly one football table-data request is made and nonce is sent as `wdtNonce`;
4. HTTP response is 2xx;
5. response is valid JSON object;
6. total historical row count is reported and `recordsTotal >= 1000` (or legacy equivalent);
7. returned row count is exactly 1;
8. first-row schema exposes no forbidden score/result field names;
9. nonce value persisted/logged/hashed = 0;
10. football row values persisted = 0;
11. target/result values materialized = 0;
12. model_fit=0 and model_score=0.

If request again returns empty/non-JSON: `C072N6_NONCE_PREFLIGHT_STRUCTURE_FAIL`. Do not adaptively repair inside N6.
If access/premium/auth fails: `C072N6_ACCESS_BLOCKED`.

## If PASS
Freeze C072-N7 before any additional table-data request. N7 may make one `length=1` metadata/schema request per fixed five-league Last-seasons table solely to measure `recordsTotal` and schema; it must preserve N2's >=4000 pooled-history gate and still persist zero football row values.

## Hard boundaries
- No bulk retrieval in N6.
- No model training/scoring.
- No K2/L2 label reuse.
- No Draw/0-0/1-1 boost.
- C070-F Confirmation1597 sealed.
- protected assets sealed.
- C073-C077 quarantined.
- formal_weight=0; no CURRENT change.
