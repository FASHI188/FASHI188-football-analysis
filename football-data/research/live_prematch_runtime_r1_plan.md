# Live Prematch Runtime R1 — Usability Closure

## Priority

P0: make the football research stack usable at question time before continuing feature-value research.

This branch must not change CURRENT, formal weights, formal model artifacts, or settled historical labels. It builds and validates an operational shadow bridge first.

## Problems to close

1. Unsupported one-off competitions (for example Community Shield) currently fail registry identity even when a valid strength reference competition exists.
2. New-season cold start fails because the formal core requires same-target-season completed matches.
3. Neutral venues are not represented by the venue-specific formal strength path.
4. V6.26 three-stage reconciliation requires upstream 1X2 and total-goal heads but question-time runtime does not close those heads.
5. PIT availability / XI evidence can be acquired, but no single runtime binds evidence freeze, strength state, probability output, and audit provenance.

## R1 operational design

- Separate `event_competition_id` from `strength_reference_competition_id`.
- Preserve exact kickoff/freeze timestamps and reject any evidence observed after freeze.
- Use a cross-season cold-start bridge only when target-season history is below the normal gate.
- Reuse existing frozen football engine mathematics for the bridge; do not invent manual probability nudges.
- For neutral venue, compute both team orientations and combine mirrored distributions symmetrically without a hand-tuned home-advantage coefficient.
- Produce auditable 1X2, 0-7+ total goals, score matrix, top scores, data lineage, bridge status, and limitations.
- Availability / probable XI are frozen and surfaced immediately; numeric mutation remains disabled until OOS value validation proves an effect.
- Market inputs are optional read-only context unless a synchronized PIT snapshot satisfies the existing market contract.

## Acceptance tests

1. Arsenal vs Manchester City, 2026-08-16 Community Shield, pre-kickoff frozen input: must return an operational-shadow probability artifact instead of `unsupported competition` / `new season cold start` failure.
2. 2026/27 Premier League Matchweek 1 with zero same-season history: must return an operational-shadow probability artifact through the cold-start bridge.
3. Normal in-season Premier League match with sufficient same-season history: runtime must route to the normal path, not the bridge.
4. Neutral-venue output must be invariant to swapping the nominal home/away labels and mirroring the result probabilities / score matrix.
5. Post-freeze evidence must hard fail.
6. No formal/CURRENT mutation; `formal_weight=0` until chronological OOS and prospective PIT gates pass.

## Promotion condition

The bridge becomes the user-facing single-match runtime only after the above engineering tests pass. Formal-model promotion remains a separate scientific/governance decision.
