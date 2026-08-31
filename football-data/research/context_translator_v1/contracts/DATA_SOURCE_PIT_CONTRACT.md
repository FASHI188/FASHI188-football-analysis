# DATA_SOURCE_PIT_CONTRACT

Status: FROZEN_CONTRACT / RESEARCH_ONLY

## Universal PIT rule
Every datum consumed by a target-match predictor MUST have `known_at < cutoff`, where cutoff is target kickoff. `known_at` is the earliest defensible time the fact became available to the model, not the time it was later downloaded. Unknown or unverifiable availability is default-deny.

Required provenance per raw fact: `source_url`, `raw_sha256`, `published_at`, `observed_at`, `retrieved_at`, `known_at`, `source_tier`, `extraction_confidence`, provider/license identifier, immutable source/version reference where available.

## Initial approved source classes
A. OpenFootball exact-commit CC0/public-domain data: schedule, kickoff, team identity aliases and prior completed match results; TEAM_ONLY role. Current target result is label-only and never predictor-visible.
B. StatsBomb Open Data at exact immutable repository/data version: prior-match events, lineups and minutes only when competition/season coverage and open-data terms are recorded. Prior-match event-derived player/tactical features become available conservatively after the prior match is completed and the dataset publication assumption used by the experiment is documented. Current-target confirmed lineup is NOT considered PIT-valid merely because a post-match open-data file contains it.
C. Official team/competition/organizer timestamped public sources: injury, suspension, expected return, coach change, lineup, venue, weather/rules facts only when an archival/publication timestamp strictly before cutoff is preserved.
D. Other weather, referee, geospatial/travel or tracking/event providers are NOT approved by this contract merely by category. They require an immutable provider entry, license/usage check and field-level known_at rule before first use. Until then their dependent layer is BLOCKED_DATA.

## Source tiers
TIER_1_OFFICIAL: competition/team/organizer official source with timestamp.
TIER_2_OPEN_STRUCTURED: approved open structured dataset with immutable version and documented license.
TIER_3_APPROVED_ARCHIVE: explicitly approved timestamp-preserving archive/source.
Anything else: DENY.

## Availability rules
- Prior match result/event: conservative availability no earlier than prior kickoff + 3h unless stronger publication evidence exists.
- Simultaneous kickoff batch: all target predictions freeze before any target-batch result is released.
- Confirmed target lineup: usable only if source publication timestamp < cutoff; otherwise route EXPECTED_LINEUP or LINEUP_UNKNOWN.
- Weather/venue/referee/competition rules: timestamp/version proving pre-cutoff availability required.
- News text: only whitelisted factual predicates; extraction output inherits source known_at and raw SHA.
- Future schedule changes, post-match corrections and target realized process events are forbidden.

## Identity and field safety
Every source record must resolve competition/team/player/match identities through the registry. Ambiguous identity => quarantine/HARD_FAIL, never fuzzy auto-merge. Unrecognized fields are ignored by default; adding a consumed field after V1 freeze is scope expansion and forbidden.

## Secrets/payment/redistribution
No Provider/Secret reads. No unauthorized paid data. Raw-source redistribution is forbidden unless license explicitly permits it. Artifacts should store hashes/manifests and derived research outputs; raw restricted material must not be committed.

## Stop conditions
Immediately stop the affected experiment on unknown license, missing immutable source identity, unverifiable known_at, source timestamp contradiction, identity collision, target-label leakage or future-data exposure.