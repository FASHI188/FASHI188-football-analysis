# V2 Data, Label and PIT Contract

Status: FROZEN BEFORE MODEL CODE

## Authorized sources
- Ended historical matches already present in the repository.
- Existing lawful public collectors at the M10 anchor, used read-only.
- New public resources requiring no login, payment, Secret, private API, or license circumvention. Each new resource must record canonical URL, retrieval UTC, raw SHA256, schema version, license/usage boundary, and a `known_at` rule before use.

## Historical-universe freeze
Before model fitting, write a canonical row ledger ordered by `(kickoff_utc, competition_id, season, canonical_home_id, canonical_away_id, fixture_id)`. Freeze SHA256 of every canonical row, aggregate digest, source-file SHA256 map, exact row count, first/last cutoff, competition-season set, schema version, and identity-registry hash. Any change invalidates the run.

## PIT rule
For target fixture cutoff T, prediction features may include only facts with verified `known_at < T`. If source granularity is date-only, use a conservative boundary that cannot expose same-day target results. Same-cutoff fixtures are one atomic predict-before-update batch. No random split.

## Strict labels
External final goals are accepted only if `type(x) is int` and `x >= 0`. Reject bool, float including `1.0`, string numerals, NaN, Inf, Decimal/Fraction wrappers, and negative values. Do not silently coerce. Final 90-minute score labels exclude extra time/penalties unless explicitly represented in a separate non-target field.

## Final-label isolation
The final holdout prediction executable receives no holdout result column, label object, label path, or in-memory row containing final goals. It reads a label-free fixture/features manifest only. It persists canonical blind predictions, fsyncs, records byte length, SHA256, fixture set, cutoff set, model/config hashes and manifest hash, then terminates.

A separate scorer process later reopens the blind file from disk, recomputes SHA256 from raw bytes, validates byte length, schema, fixture/cutoff sets and manifest, and only then opens the independently stored label source. A syntactically valid 64-character string is never sufficient evidence of freezing.

## Final holdout
Final holdout labels are inaccessible to tuning/selection. No threshold, hyperparameter, layer retention, calibration family, score dependence rule, or class treatment may change after `FINAL_RULE_FROZEN`. A failed final holdout ends in `NOT_PROMOTED`; it cannot be mined for iterative retuning. New research requires a new preregistered development interval and new untouched holdout.

## Market isolation
Pure lane input schemas contain no odds/price/market fields. A separate assisted lane may use verified prematch PIT market snapshots with source/hash/observed_at. It is independently evaluated and never used to claim pure-model gains.