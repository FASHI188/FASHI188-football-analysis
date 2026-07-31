# Daily single-match test scope

The test suite covers target-result leakage blocking, strict pre-freeze history selection, live-mode identity/freshness gates, fixed A-H rendering, and a full invocation of the existing formal engine against the deterministic offline scenario.

The workflow also reruns existing formal-engine and prediction-pipeline regression tests, verifies probability conservation, checks the exact evidence HEAD, asserts zero provider requests and zero API-key access, and rejects model/data/config/CURRENT diffs.
