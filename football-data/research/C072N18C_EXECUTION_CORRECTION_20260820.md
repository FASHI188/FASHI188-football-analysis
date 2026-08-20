# C072-N18C execution correction — zero-label gzip receipt only

The first N18C workflow attempt stopped before any DEVELOPMENT outcome request.

Observed boundary at stop:
- N18B2 status reproduced PASS;
- eligible 611, selected 550, DEVELOPMENT 400, CONFIRMATION_SEALED 150;
- dev400 IDs SHA256 reproduced exactly `55181a078d39d9ac53881aa0c377d6c6cb819c06053bd75609841a13caa1dbdf`;
- confirmation150 IDs SHA256 reproduced exactly `774be269e30254af29614210401b52c23b0f3a4e79a7945e98014d50590ea90f`;
- target result values materialized = 0;
- N18C model step was skipped.

The sole failure was asserting the SHA256 of the gzip container `c072n18b_target550_zero_label.jsonl.gz`. The parent builder writes gzip with a runtime timestamp in the gzip header, so compressed-byte SHA is not reproducible even when the decompressed JSONL payload is identical. This is a transport-receipt defect, not a scientific/data change.

Correction:
- do not use gzip-container bytes as the N18C zero-label reproducibility gate;
- use SHA256 of the decompressed JSONL bytes instead;
- authoritative decompressed semantic payload SHA256 from the already-frozen N18B2 artifact is `b72fd9225d51178db533bee129bc9406a794d127b511bdcaed4b65ffd2339b9a`;
- keep dev400 and confirmation150 ID hashes as independent identity gates.

No scientific term changes: same 400 DEVELOPMENT outcomes, same 150 sealed confirmation IDs, same market anchor, 16 features, NB2 family, folds, optimizer, lambda, metrics, PASS gates and stopping rule. No outcome labels were accessed before this correction.