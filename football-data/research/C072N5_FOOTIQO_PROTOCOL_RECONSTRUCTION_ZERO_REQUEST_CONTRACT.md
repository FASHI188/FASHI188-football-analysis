# C072-N5 — Footiqo get_wdtable protocol reconstruction, zero request

## Lineage
- football3 only.
- Parent C072-N4 used its single permitted EPL Last-seasons request and received HTTP 200 with an empty body.
- N4 resolved the correct historical odds table (`wpDataTable 545`) and site-hosted action literal `get_wdtable`, but its frozen request body cannot be modified/retried inside N4.
- N5 therefore performs **zero football table-data requests** and reconstructs the exact page/plugin transport contract before any second request is considered.
- C073-C077 remain quarantined.

## Fixed questions
For EPL Historical Odds Last-seasons table 545 only, establish from Footiqo-hosted HTML/static JS:
1. whether table 545 itself is configured with server-side/AJAX processing;
2. exact admin-ajax action/query parameter names used for `get_wdtable`;
3. whether a public nonce/security token is required, and token **name/presence only** (do not persist token values);
4. exact DataTables request parameter families used (`draw/start/length/columns/search/order/...`);
5. exact backend column `name` identifiers associated with table 545, if explicitly embedded, and whether they differ from visible headers;
6. any table-specific additional public transport keys (e.g. range separator/filter data/tableWpId) required by the initialized table.

## Allowed network requests
GET only:
- EPL page `https://footiqo.com/database/leagues/england-premier-league/`;
- same-site static JS assets referenced by that page whose URL contains `wpdatatable`, `datatable`, `wpdt`, or `buttons`.

Forbidden:
- POST;
- GET/POST to `admin-ajax.php`;
- any request carrying `action=get_wdtable`, `table_id`, pagination/filter/search parameters;
- any other football row-data endpoint.

## Extraction discipline
Persist only structured protocol metadata, never arbitrary source snippets:
- booleans for known transport tokens;
- table IDs and DOM IDs;
- serverSide boolean if directly recoverable;
- action name and query/body **key names only**;
- nonce/security **key names and presence only**, never values;
- column backend names/schema identifiers only;
- explicit DataTables parameter-family names;
- static asset URLs/version strings.

No football row values, odds values, dates, teams, scores, results or arbitrary surrounding JavaScript text may be persisted.

## Frozen PASS gate
Terminal `C072N5_PROTOCOL_RECONSTRUCTION_PASS` only if ALL:
1. EPL page HTTP 2xx;
2. table 545 is directly associated with the Historical Odds Last-seasons table;
3. table 545's own server-side/AJAX status is resolved;
4. `get_wdtable` request URL construction is resolved from Footiqo-hosted code/config, including where `action` and `table_id` are placed;
5. DataTables request parameter families are resolved sufficiently to reproduce the browser request structure;
6. nonce/security requirement is resolved as required/not-required/present-but-unresolved without persisting a value;
7. backend column-name schema is either resolved or explicitly established as absent/not-required from the page initialization;
8. football table-data endpoint requests made = 0;
9. target/result values materialized = 0; model_fit=0; model_score=0.

If configuration remains ambiguous: `C072N5_PROTOCOL_DETAIL_NOT_ESTABLISHED`.
If page/static assets are blocked: `SOURCE_ACCESS_BLOCKED`.

## If PASS
Freeze C072-N6 before any second football table-data request. N6 may make exactly one new EPL Last-seasons `length=1` request using only the newly reconstructed browser-equivalent transport fields. No adaptive retry is allowed.

## Hard boundaries
- Do not lower N2/N4 gates.
- No K2/L2 label reuse.
- No model training/scoring.
- C070-F Confirmation1597 sealed.
- protected assets sealed.
- C073-C077 quarantined.
- formal_weight=0; no CURRENT change.
