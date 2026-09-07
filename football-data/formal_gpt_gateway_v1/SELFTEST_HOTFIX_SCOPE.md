# Football3 formal runner selftest control-flow hotfix

This hotfix is orchestration-only.

- `COMMITTED_SELFTEST_REQUEST` remains a non-prediction `cache_reuse_probe` with no `match`.
- `SKIPPED_NON_PREDICTION_MODE` must not enter the bundle-dependent formal prediction gateway.
- Non-prediction push selftest writes an explicit PASS/SKIP receipt and does not claim a prediction.
- Real `predict` mode still requires durable selector success, a valid bundle, a PASS formal summary, an integrity-guard PASS, and a non-empty prediction SHA consistent with the formal receipt.
- Gateway, durable selector, effective-evidence guard, fail-closed behavior, model/CURRENT, and 75/25 fusion weights are not relaxed or changed.
