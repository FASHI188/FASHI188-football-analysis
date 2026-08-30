# V2 Reuse Boundary

Status: FROZEN BEFORE IMPLEMENTATION

## Allowed read-only reuse
Generic historical raw/processed match files; lawful collectors already present at M10; canonical identity/alias concepts; generic deterministic hashing/PIT/lineage ideas; generic metric definitions; public mathematical literature; V1 reports/Artifacts/code only as research reference and comparator behavior.

## Forbidden in V2 core
V1/V500/R43 fitted parameters, dynamic states, prediction matrices, outputs as labels/features, calibration temperatures, hidden market priors, copied model implementation used as V2 engine, wrappers/adapters that call legacy model code, or branch ancestry after M10. V1 and V500 may only be invoked in an isolated evaluation comparator after V2 prediction is frozen.

## Independence proof
V2 core lives only in `football-data/new_engine_v2_joint_score/`. CI statically rejects legacy model module names/imports and market tokens in pure-core modules. Runtime pure input schema is allowlisted and excludes odds/market fields. Workflow verifies the branch parent chain begins at the exact M10 anchor and that every changed path is whitelisted.

## Research references
The published Dixon-Coles, Mar-Co and Sarmanov mathematical structures may be implemented independently from papers; this is not reuse of repository legacy code.