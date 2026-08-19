# C072-N11 — Betfair Hub A-League All Markets header-only source audit

Project: football3 only
Parent: `C072N11_DYNAMIC_MULTILINE_OU_ZERO_LABEL_SOURCE_CONTRACT.md`

## Candidate source
Official public Betfair Data Scientists repository:
`betfair-datascientists/betfair-datascientists.github.io`
Frozen revision: `9fe7fb127cd05316dbd438fe0e5be82c5c3ed536`

Candidate files, exactly:
- `docs/data/assets/A-League_2020-2021_All_Markets.csv`
- `docs/data/assets/A-League_2021-2022_All_Markets.csv`
- `docs/data/assets/A-League_2022-2023_All_Markets.csv`
- `docs/data/assets/A-League_2023-2024_All_Markets.csv`
- `docs/data/assets/A-League_2024-2025_All_Markets.csv`
- `docs/data/assets/A-League_2025-2026_All_Markets.csv`

The listing page identifies these as A-League `All Markets` public CSVs. No football3 row values have been inspected before this contract.

## Global-consumption check before row inspection
Search of the shared GitHub PR history for `A-League Betfair` and `betfair-datascientists` returned no matching prior experiment records. Search of the shared Airtable maintenance log for `A-League Betfair` returned no record. This records only the searches performed; it is not a claim that unsearched external history cannot exist.

## This phase is header-only
Allowed operations:
- download the six exact pinned files;
- compute byte size and SHA256 over raw bytes;
- decode and record only the first CSV record (header);
- record file retrieval success/failure.

Forbidden in this phase:
- decoding any data row after the header;
- reading any outcome, result, score, settlement or winner value;
- counting market types from data rows;
- model fitting/scoring;
- joining to any match result source.

## Ruling
`ALEAGUE_HEADER_PASS` if at least one pinned All Markets file is retrieved, hashed and its header decoded successfully.
`ALEAGUE_HEADER_STOP` otherwise.

A PASS authorizes only a versioned zero-label schema/coverage contract written after the header is known. It does not authorize target access.
