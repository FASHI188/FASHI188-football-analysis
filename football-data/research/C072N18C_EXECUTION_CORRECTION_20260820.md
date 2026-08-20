# C072-N18C execution corrections — transport/repro only

## Correction 1 — gzip receipt nondeterminism
The first N18C workflow attempt stopped before any DEVELOPMENT outcome request.

Observed boundary at stop:
- N18B2 status reproduced PASS;
- eligible 611, selected 550, DEVELOPMENT 400, CONFIRMATION_SEALED 150;
- dev400 IDs SHA256 reproduced exactly `55181a078d39d9ac53881aa0c377d6c6cb819c06053bd75609841a13caa1dbdf`;
- confirmation150 IDs SHA256 reproduced exactly `774be269e30254af29614210401b52c23b0f3a4e79a7945e98014d50590ea90f`;
- target result values materialized = 0;
- N18C model step was skipped.

The failure was asserting SHA256 of gzip-container bytes. Runtime gzip header timestamps make the compressed-byte SHA nondeterministic even when decompressed JSONL is identical.

Correction:
- use SHA256 of decompressed JSONL bytes;
- authoritative semantic payload SHA256 = `b72fd9225d51178db533bee129bc9406a794d127b511bdcaed4b65ffd2339b9a`;
- retain dev400 and confirmation150 ID hashes as independent identity gates.

## Correction 2 — historical result-table resolver
The next workflow reproduced the semantic zero-label payload and both split hashes exactly, then stopped before the first DEVELOPMENT result POST.

Observed boundary:
- semantic target550 SHA reproduced exactly;
- confirmation requests = 0;
- confirmation result values = 0;
- DEVELOPMENT result values = 0;
- model fit/score not reached.

The failure was `result table resolution expected 1 got 0 season=2024/2025`. The implementation incorrectly required the server-side historical result table's first rendered HTML rows to directly contain the requested target season. Footiqo does not guarantee that for server-side tables.

Correction:
- use the already-established N17 table-resolution protocol: among exact `RESULT_HEADERS` tables, select the unique table whose visible sample has the earliest starting season;
- after resolving that historical table, request the frozen season and one frozen DEVELOPMENT ID through server-side column filters;
- every result POST remains single-ID target-only; confirmation IDs remain forbidden at request layer.

No scientific term changes in either correction: same 400 DEVELOPMENT identities, same 150 sealed confirmation identities, same market anchor, 16 features, NB2 family, folds, optimizer, lambda, metrics, PASS gates and stopping rule. No DEVELOPMENT or confirmation outcome labels were accessed before these corrections.