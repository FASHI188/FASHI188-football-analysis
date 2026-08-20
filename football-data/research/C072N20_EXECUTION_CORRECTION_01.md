# C072-N20 execution correction 01

Authoritative failed precursor:
- run `32326379971`
- job `96298363218`
- HEAD at execution: `626a5fbffda25889b334a7c6fda4e8ca689dff0e`

## Failure boundary
The frozen N20 evaluator completed source fetch and passed the exact-1000 coverage branch: execution reached `calibrated_tails(test,models)` after the code path that would have returned `C072N20_P1000_STOP_COVERAGE` for any join count other than 1000.

Therefore the exact locked N20 1000 target labels were numerically opened in this failed precursor and are **globally consumed from this run onward**.

No scientific metric was computed or emitted. The failure occurred at:
`y=test.T.to_numpy(int)`
where pandas resolves `DataFrame.T` as the transpose property rather than the column named `T`, causing conversion of identity strings to int and raising `ValueError`.

## Only authorized correction
Execute the exact frozen evaluator after one textual substitution only:
- from: `y=test.T.to_numpy(int)`
- to: `y=test['T'].to_numpy(int)`

No identity, target rows, training rows, prices, model, calibration C, PAVA rule, continuation rule, metrics, bootstrap seed, domains, gates, stopping rule or sealed boundary may change.

The corrected run is an **engineering replay / reproduction on already-consumed N20 labels**, not fresh evidence. Its scientific metrics are the first metrics for the frozen N20 hypothesis and may be adjudicated under the already-frozen contract, but must never be described as blind/pristine/independent confirmation.
