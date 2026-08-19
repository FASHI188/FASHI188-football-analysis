# C072-N7R1 — Five-league metadata engineering replay

## Why this replay exists
- Parent N7 contract was frozen and executed in run `32244394749`.
- The evaluator reached `persist(result)` after its five-league execution logic, but the evidence file was never written because a local safety assertion matched the benign metadata key name `nonce_values_persisted_or_logged`.
- N7 therefore has **no source PASS/FAIL verdict**. This is an evidence-persistence engineering failure, not a coverage/protocol/scientific failure.

## Binding replay rule
N7R1 must reproduce the exact N7 network/scientific recipe with NO changes to:
- five fixed league pages;
- Last-seasons table resolution;
- N3 expected table-ID consistency check;
- hidden server-side nonce resolution;
- exactly one `length=1` request per league;
- query/body/action/column descriptors;
- metadata fields interpreted;
- all frozen N7 coverage/schema gates.

The ONLY allowed change is evidence persistence safety logic:
- remove the substring search that confuses a metadata key name with a secret;
- explicitly reject forbidden secret-bearing dictionary keys (`nonce_value`, `raw_nonce`, `request_body`, `wdtNonce`) and never persist nonce values/request bodies.

No result from the failed N7 run may be used to change thresholds, table selection, body fields, league list or gates.

## Replay classification
- The five new one-row requests are an engineering replay of public metadata because the first run's result was lost after execution.
- They are not an independent scientific confirmation and do not create a new information family.
- N7R1 inherits N7's terminal gate names and may produce `C072N7_FIVELEAGUE_METADATA_PASS`, `C072N7_PROTOCOL_DRIFT_STOP`, `C072N7_ACCESS_BLOCKED`, or `C072N7_METADATA_COVERAGE_FAIL`.

## Hard boundaries
- Max football table-data requests = 5.
- No bulk retrieval.
- No football row values persisted.
- No nonce values persisted/logged/hashed.
- Target/result values materialized = 0.
- Model fit/score = 0.
- C070-F Confirmation1597 sealed.
- protected assets sealed.
- C073-C077 quarantined.
- formal_weight=0; no CURRENT change.
