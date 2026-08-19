# C072-N4 — Footiqo anonymous AJAX minimal preflight

## Lineage
- football3 only.
- Parent C072-N3 terminal: `C072N3_PUBLIC_AJAX_CONFIG_PASS`.
- N3 established public `admin-ajax.php`, wpDataTables 7.3.4, server-side/AJAX processing, and EPL historical-odds table identifiers without requesting any football table-data endpoint.
- C073-C077 remain quarantined.

## Objective
Make the **first and only** football table-data request in this stage, against the EPL **Last seasons Historical Odds** table, solely to establish anonymous transport viability, total row metadata, and response schema shape.

This is NOT bulk retrieval and NOT a model experiment.

## Fixed page and endpoint family
Page:
- `https://footiqo.com/database/leagues/england-premier-league/`

Potential endpoint family established by N3:
- `https://footiqo.com/wp-admin/admin-ajax.php`

The exact table ID and exact wpDataTables action MUST be resolved from the current public page/static Footiqo JS before the request. Do not hard-code an unverified table ID/action from third-party examples.

## Zero-label table resolution
1. Slice HTML beginning at literal `Historical Odds: 1X2, Over/Under Goals, BTTS`.
2. Identify tables whose headers contain O/U 1.5, 2.5 and 3.5 pairs.
3. For each matching table, materialize only table attributes/header names and visible `Season` cells.
4. Select the **Last seasons** odds table as the matching table whose visible Season values contain a non-current historical season (e.g. 2015/2016) and are not restricted to the current 2025/2026 season.
5. Resolve that table's numeric wpDataTables/table ID only from its own DOM/data attributes and directly-associated inline initialization tokens.

No score/result fields may be parsed from any other page section.

## Protocol resolution
Allowed before the request:
- GET the EPL HTML page;
- GET same-site wpDataTables/DataTables static JS referenced by that page;
- search those texts for exact literal protocol/action/config tokens only.

The request is permitted only if the site's own HTML/JS establishes:
- an admin-ajax URL;
- the exact selected Last-seasons table ID;
- a wpDataTables data action (expected form may resemble `get_wdtable`, but it must be found in Footiqo-hosted code/config, not assumed);
- DataTables server-side parameter names.

If any of these is unresolved: STOP with `C072N4_PROTOCOL_NOT_RESOLVED` and send **zero** football data requests.

## Single allowed data request
Exactly one request maximum:
- method: POST;
- same-origin Footiqo admin-ajax endpoint;
- selected EPL Last-seasons historical-odds table only;
- `draw=1`, `start=0`, `length=1`;
- empty global search;
- column descriptors derived from the selected odds table headers only;
- `X-Requested-With: XMLHttpRequest`;
- no authentication/login/premium token invention.

No retry with a different table ID/action/body after seeing the response. A malformed/blocked response ends N4.

## Response handling (binding privacy/scientific guard)
Do NOT persist football row values.

Allowed persisted response metadata only:
- HTTP status/content type/response bytes;
- JSON top-level key names;
- `draw`, `recordsTotal`, `recordsFiltered` (or legacy equivalent total-count keys);
- returned data container type and row count;
- if first returned row is an object: **field names only**, never values;
- if first returned row is an array: array length only, never values;
- whether any response field name matches forbidden score/result patterns.

Forbidden persisted values:
- team names;
- dates;
- scores/results;
- odds values;
- any first-row data values.

## Frozen PASS gate
Terminal `C072N4_MINIMAL_AJAX_PREFLIGHT_PASS` only if ALL:
1. EPL page and permitted static assets are accessible;
2. one Last-seasons historical-odds table is uniquely resolved;
3. site-hosted code/config establishes the data action and admin-ajax URL;
4. exactly one football table-data request is made;
5. request HTTP status is 2xx;
6. response is valid JSON;
7. total historical row count is reported and `recordsTotal >= 1000` (or equivalent);
8. returned data row count is <=1 and >=1;
9. response row schema has no forbidden score/result field names;
10. no football row values are persisted;
11. target/result values materialized = 0;
12. model_fit=0 and model_score=0.

If request is anonymous-auth/premium blocked: `C072N4_ACCESS_OR_PREMIUM_BLOCKED`.
If request succeeds but total/schema conditions fail: `C072N4_PREFLIGHT_STRUCTURE_FAIL`.
Do not lower these gates or send a second request.

## If PASS
Freeze C072-N5 before any additional AJAX request. N5 may audit all five leagues' historical odds row counts and multi-line coverage in a bounded zero-label retrieval plan, preserving the original N2 >=4000 pooled-history gate.

## Hard boundaries
- No model training/scoring.
- No K2/L2 result-label reuse.
- No Draw/0-0/1-1 boost.
- C070-F Confirmation1597 sealed.
- protected assets sealed.
- C073-C077 quarantined.
- formal_weight=0; no CURRENT change.
