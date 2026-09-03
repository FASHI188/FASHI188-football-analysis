# Formal Fast Prediction Runtime V1 — Scope Revision (2026-09-03)

This document appends a user-authorized scope revision to the existing Formal Fast Prediction Runtime construction record. It does not delete, rewrite, or convert any earlier contract, failed gate, or evidence into a pass.

## Accepted engineering scope for this round

Only the following are acceptance targets:

1. deterministic full-state rebuild from already-authorized frozen historical inputs using the unchanged formal Fusion V2 components;
2. complete cache serialization / validation / restoration with model, source-data, cutoff, schema, manifest and SHA bindings and no precision loss;
3. automatic `FAST_PATH` / `FULL_REBUILD_PATH` routing, with cache failure, staleness, incomplete delta, unknown delta, and corrupted cache failing over to trusted full rebuild rather than being silently used;
4. 300 mechanically selected frozen-historical fixture equivalence checks between full replay and cache replay, including complete 1X2 and score-matrix equivalence at `1e-12` absolute tolerance, route/fallback/cutoff/cold-start/uncertainty identity, V1 exact fallback identity, same-kickoff isolation and target-label isolation;
5. isolated per-fixture input and prediction receipt handling.

## Explicitly not prerequisites for this engineering acceptance

The engineering equivalence test does **not** require acquisition of post-2025/26 XG, complete latest-season league data, injuries, suspensions, predicted/confirmed lineups, bench, coach, formation/tactics, rest/schedule density, weather/pitch data, a new provider, a paid dataset, or a secret.

Those remain per-match input-availability questions. If a future target cutoff is later than the end of the trusted frozen state and no complete verified delta is available, the runtime must report `FORMAL_INPUT_DATA_INCOMPLETE`; it must not fabricate a formal probability and must not describe unknown XG completeness as verified insufficiency.

## Scientific immutability

This revision does not alter the formal model, scientific contracts, 75% Historical XG + 25% Frozen V1 weights, Frozen V1 parameters, Historical XG parameters, fallback semantics, CURRENT, production pointer, formal enablement, V3, or any prior scientific score/gate.

Both runtime paths must call the same immutable formal model modules. No hand-written approximation, alternate lambda, simplified state, widened numerical tolerance, or post-view repair is permitted.

## Claim boundary

Passing this engineering scope means only:

> Cache and full calculation are equivalent on frozen historical inputs; automatic switching is reliable; awaiting independent engineering review.

It does **not** mean that all data for any current fixture are complete, that the new runtime is formally enabled, or that an arbitrary new ChatGPT conversation can already invoke the runtime.
