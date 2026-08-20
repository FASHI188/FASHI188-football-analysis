# C079-A2 Frozen Contract — Footiqo Full-Table Retrieval Endpoint Audit

Status: engineering/source-governance only / `formal_weight=0` / zero-label.

## Purpose
C079-A established that same-match O/U2.5, 3.5 and 4.5 prices are present, fully numeric and perfectly nested on the public HTML sample, but the public page body exposes only a small paginated subset (234 pooled rows across five fixed domains), failing the frozen volume gate. C079-A2 may investigate only the retrieval mechanism needed to access the complete Odds table.

## Fixed diagnostic target
Primary page only:
`https://footiqo.com/database/leagues/argentina-liga-profesional/`

No result/score row values may be parsed or persisted.

## Allowed reads
- HTTP response headers and raw HTML structure;
- `<script src>` URLs and inline script text;
- table/form/button/link attributes;
- JavaScript files loaded by the page when hosted on `footiqo.com` or its same-site static origin;
- string/config snippets containing any of: `DataTable`, `DataTables`, `ajax`, `serverSide`, `processing`, `pageLength`, `lengthMenu`, `buttons`, `csv`, `excel`, `export`, `admin-ajax`, `wp_ajax`, `wp-json`, `/api/`, `download`, `historical`, `odds`.

## Forbidden reads / outputs
- no table-body match values;
- no FTHG/FTAG/FTR or score fields;
- no result-derived targets;
- no odds-row export in this diagnostic;
- no model fit;
- no credentials, login automation, premium bypass or access-control circumvention.

The script must sanitize its durable output to endpoint/config metadata and bounded context snippets only.

## Terminal classification
- `ENDPOINT_DISCOVERED`: at least one concrete callable full-table AJAX/export/download endpoint or deterministic client-side complete-data payload reference is found.
- `CLIENT_SIDE_FULL_DATA_INDICATED`: scripts show all rows are already embedded/client-side but current parser selected only visible DOM rows; requires a separate zero-label retrieval implementation.
- `NO_PROGRAMMATIC_ENDPOINT_FOUND`: no usable public full-table retrieval mechanism found without authentication/premium access.
- `ENGINEERING_ERROR`: technical failure before a defensible classification.

No scientific PASS/FAIL is produced here. C079-A's 3000-row market gate remains unchanged.
