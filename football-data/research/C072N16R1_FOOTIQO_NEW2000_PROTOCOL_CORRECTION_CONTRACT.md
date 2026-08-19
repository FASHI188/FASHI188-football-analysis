# C072-N16R1 — Footiqo new-2000 protocol-only correction

## Parent adjudication
- Parent N16 run `32261918325` is permanently `C072N16_FOOTIQO_NEW2000_ZERO_LABEL_DOWNLOAD_STOP`.
- N16 made **0** table-data POST requests, retrieved 0 odds rows and materialized 0 result/target values.
- Failure cause: each current Footiqo league page contains two exact-schema odds tables (current-season and historical-season); N16 resolver required exactly one exact-schema table and therefore fail-closed with `ODDS_TABLE_PROTOCOL_DRIFT` on all four pages.

## Authorized correction only
N16R1 changes only table disambiguation. All N16 scientific/data choices remain frozen:
- same four source pages: Turkey Super Lig, Greece Super League, Brazil Serie A, USA MLS;
- same exact 22 odds columns;
- same full-inventory retrieval protocol and <=80 POST budget;
- same exact duplicate/conflict rule;
- same SHA256(identity) ascending exact-2000 selection rule;
- same coverage gates;
- zero result/target materialization;
- zero model fit/score;
- all seals/quarantine unchanged.

## Historical table resolver
After the fixed heading, enumerate tables with the exact 22-column schema. For each candidate, inspect only its visible `Season` cells (identity metadata, not outcomes). A candidate is classified as historical iff at least one visible season string begins with a four-digit start year <=2024. N16R1 requires exactly one historical candidate. Its `data-wpdatatable_id` is used with the corresponding runtime nonce.

No fallback by table order, no hard-coded new table ID, no result-table inspection and no source substitution are allowed.

## Terminal
- PASS: `C072N16R1_FOOTIQO_NEW2000_ZERO_LABEL_DOWNLOAD_PASS`
- otherwise: `C072N16R1_FOOTIQO_NEW2000_ZERO_LABEL_DOWNLOAD_STOP`

Parent N16 remains STOP regardless of N16R1 result.
