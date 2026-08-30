# V2 Forward Protocol

Status: PREREGISTERED; DORMANT UNTIL HISTORICAL FINAL PASS

V2 forward evidence starts at zero. V1's 38 prospective rows are explicitly ineligible and cannot be copied, seeded, counted, or compared as V2 prospective evidence.

## Activation gate
No forward workflow may enroll a fixture unless final historical status for V2 is `MODEL_CANDIDATE_PASSED` and exact HEAD/Artifact acceptance has succeeded. Otherwise forward status is `BLOCKED_SCIENTIFIC_GATE`.

## Ledger schema
Top-level and nested schemas are explicit allowlists with recursive default deny. No unknown keys. Result-like fields including `final_score_90`, `result`, `nested result`, `settlement`, score aliases, labels, outcomes, postmatch state, or unknown provider payload are rejected recursively.

Each row includes schema_version, fixture/provider identity, competition, canonical teams, kickoff, observed_at, cutoff rule, source hashes, model/config hashes, prediction matrix/1X2/uncertainty, `labels_present=false`, `outcomes_read=false`, `previous_row_hash`, and recomputed `row_hash`. Hash chain starts from a frozen genesis hash. On restore, every row is canonicalized and row_hash plus previous_row_hash is recomputed/verified; no stored hash is trusted.

## Checkpoints
30 = operational stability only; 100 = trend observation only; 300 = stability confirmation. Checkpoint verification rereads and rehashes the complete ledger prefix and validates chain, schemas, source/model hashes and prefix bytes; it never trusts stored row_hash lists alone.

## Artifact restore
Before restore: verify GitHub Artifact digest, downloaded ZIP SHA256, CRC of every entry, artifact manifest HEAD/parent/run/attempt, complete file bytes and hashes, model lock, ledger chain and checkpoint prefix. Any mismatch fails closed.

## Automation evidence
Automatic operation may be claimed only after an actual GitHub schedule or ChatGPT automation object is created and re-read with identifier/schedule or actual scheduled run evidence. Until then status is `AUTOMATION_NOT_ESTABLISHED`.

No forward result labels may be read without separate future authorization.