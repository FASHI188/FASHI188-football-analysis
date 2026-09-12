# Historical Sealed Cutoff Delta Replay Hotfix v1

Status: BLOCKED_ON_STRICT_PIT_EVIDENCE_AND_FROZEN_UCL_SCOPE

- Canonical integration branch: `football3/formal-gpt-runner-integration-v1`
- Exact base HEAD at construction start: `0e102ac3689185d3378bab3bc416ccec20d519de`
- Hotfix branch: `football3/historical-sealed-cutoff-delta-replay-hotfix-v1`
- Draft PR: `#369`
- Failure evidence: workflow run `34249505642`, job `102140011529`
- Target defect: an older durable state cannot advance through strict PIT immutable deltas to the historical target cutoff and incorrectly falls into current/live acquisition.

## Frozen scope

Repair sealed cross-cutoff historical advancement and durable sealing only. Preserve model, CURRENT, mixture weights, thresholds, PR #341, selector semantics outside the failing direct path, and all production activation boundaries.

## Direct failure-chain finding

The failing Udinese–Lazio request targets cutoff `2026-09-07T17:45:00+00:00`. The mechanically selected eligible durable state is from workflow run `34145305899`, artifact `10027448667`, with state cutoff and max source observation `2026-09-04T13:19:44+00:00`.

The required strict PIT advancement interval is therefore:

`2026-09-04T13:19:44+00:00 -> 2026-09-07T17:45:00+00:00`

The existing prospective acquisition path rejects this replay because current acquisition starts after the historical target cutoff. This rejection is correct and must not be weakened.

The relevant retained durable receipts/states preserve delta hashes and source metadata but do not preserve the exact immutable delta record bytes needed to reconstruct that legacy interval source-silently. No eligible pre-target sealed state covers the interval. Therefore the legacy Udinese–Lazio interval cannot be truthfully promoted to strict PIT immutable replay from currently retained evidence without a post-cutoff reconstruction fetch or fabricated provenance.

## Continuous-sealing design implication

Future prospective acquisitions must persist the exact validated delta records, their canonical hash/source-set hash, observation metadata, and contiguous from/to bounds inside the content-addressed durable state lineage so later historical advancement can consume only already-sealed segments and fail closed on any gap. The historical replay adapter must never fetch current/live sources when strict sealed coverage is absent.

No code has been changed to weaken the existing retrospective/prospective trust boundary while the legacy evidence gap remains unresolved.

## Independent frozen-scope incompatibility: UCL

The requested eight-domain hard gate includes UCL. The current formal runtime scope does not include the repository competition identifier `UEFA_ChampionsLeague`, and durable-state governance rejects competitions outside that formal scope before prediction. Under the explicit prohibition on changing model/formal scope/route semantics or substituting an auxiliary model, a valid formal UCL Receipt + prediction SHA cannot be produced by this hotfix.

Accordingly, the requested eight-domain condition cannot truthfully reach `missing=0` under the frozen contract as currently stated.

## Required gates

- Eight-domain historical replay: ENG/ESP/GER/ITA/FRA/UCL/J1/K1, all completed fixtures in the governed validation interval, valid Receipt + prediction SHA for every fixture, `missing=0`, `live_refetch=0`, `post_cutoff=0`.
- Permanent regressions: Udinese–Lazio and Hamburg–Mainz.
- Preserve seven-domain, two independent sealed deterministic, prospective, and production-300 regressions.

## Current mechanical disposition

- Draft PR persists construction and this blocker receipt.
- No model, CURRENT, 75/25, threshold, PR #341, activation, Ready/merge, or Airtable change has been made.
- No post-cutoff/live reconstruction has been mislabeled as strict PIT evidence.
- No probability or prediction SHA has been fabricated from the failed legacy replay.
