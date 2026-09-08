# Historical Sealed Cutoff Delta Replay Hotfix v1

Status: CONSTRUCTION_STARTED

- Canonical integration branch: `football3/formal-gpt-runner-integration-v1`
- Exact base HEAD at construction start: `0e102ac3689185d3378bab3bc416ccec20d519de`
- Hotfix branch: `football3/historical-sealed-cutoff-delta-replay-hotfix-v1`
- Failure evidence: workflow run `34249505642`, job `102140011529`
- Target defect: an older durable state cannot advance through strict PIT immutable deltas to the historical target cutoff and incorrectly falls into current/live acquisition.

## Frozen scope

Repair sealed cross-cutoff historical advancement and durable sealing only. Preserve model, CURRENT, mixture weights, thresholds, PR #341, selector semantics outside the failing direct path, and all production activation boundaries.

## Required gates

- Eight-domain historical replay: ENG/ESP/GER/ITA/FRA/UCL/J1/K1, all completed fixtures in the governed validation interval, valid Receipt + prediction SHA for every fixture, `missing=0`, `live_refetch=0`, `post_cutoff=0`.
- Permanent regressions: Udinese–Lazio and Hamburg–Mainz.
- Preserve seven-domain, two independent sealed deterministic, prospective, and production-300 regressions.

This file is a construction receipt for the Draft PR and will be updated at mechanically verifiable milestones.
