# C072-N3 — Footiqo wpDataTables/AJAX backend zero-label discovery

## Lineage
- football3 only.
- Parent C072-N2 terminal: `STOP_MULTILINE_SOURCE_COVERAGE` because public static HTML exposed only 205 rows, despite 100% five-line O/U coverage and 100% de-vig monotonicity.
- Do not lower N2 coverage gates.
- C073-C077 remain quarantined.

## Question
Does the public Footiqo league page expose a no-login wpDataTables/server-side transport configuration that can be audited in a later stage for full historical multi-line O/U coverage?

This stage is configuration discovery only. It MUST NOT request any football table-data AJAX endpoint and MUST NOT parse any football result/score field.

## Fixed pages
- https://footiqo.com/database/leagues/england-premier-league/
- https://footiqo.com/database/leagues/spain-laliga/
- https://footiqo.com/database/leagues/germany-bundesliga/
- https://footiqo.com/database/leagues/italy-serie-a/
- https://footiqo.com/database/leagues/france-ligue-1/

## Allowed network requests
1. GET the five fixed HTML pages.
2. GET same-site/static JavaScript asset URLs referenced by those pages ONLY when the URL/path contains one of: `wpdatatable`, `datatable`, `wpdt`, `buttons`.

Forbidden in N3:
- POST requests;
- requests to `admin-ajax.php` or any discovered table-data endpoint;
- requests containing pagination/table/filter/action parameters intended to return football rows;
- score/result APIs;
- login/auth/premium endpoints.

## Allowed extraction
From raw HTML/JS text only, extract literal configuration tokens:
- WordPress AJAX URL/path (e.g. `admin-ajax.php`);
- wpDataTables/DataTables related script asset URLs;
- table numeric IDs / DOM IDs;
- boolean/server-side markers;
- AJAX `action` names if literally embedded in configuration/code;
- parameter/key names used by the transport;
- whether table tools/export buttons are configured.

Do not store arbitrary surrounding snippets; this prevents accidental materialization of football target/result values that may coexist in page source.

## Frozen PASS gate
`C072N3_PUBLIC_AJAX_CONFIG_PASS` only if ALL:
1. all five pages return HTTP 2xx;
2. wpDataTables/DataTables fingerprint is found on all five pages;
3. a public WordPress AJAX URL/path is identified;
4. at least one numeric/DOM table identifier is identified for every page;
5. server-side/AJAX table processing evidence is identified on at least four pages;
6. at least one plausible wpDataTables table-data action/config key is identified from HTML or permitted static JS assets;
7. no football data endpoint is invoked;
8. target/result values materialized = 0; model_fit = 0; model_score = 0.

If wpDataTables is present but no public data-transport configuration can be established, terminal `C072N3_AJAX_CONFIG_NOT_ESTABLISHED`.
If pages/assets are blocked, terminal `SOURCE_ACCESS_BLOCKED`.

## If PASS
Freeze C072-N4 BEFORE making the first request to the discovered table-data endpoint. N4 must request only the minimum pagination/schema metadata needed to prove historical row count and multi-line odds field coverage, and must explicitly prevent result-label use.

## Hard boundaries
- C070-F Confirmation1597 remains sealed.
- protected assets remain sealed.
- C073-C077 remain quarantined.
- K2/L2 labels are not used for source selection/tuning.
- no Draw/0-0/1-1 boost.
- formal_weight=0; no CURRENT change.
