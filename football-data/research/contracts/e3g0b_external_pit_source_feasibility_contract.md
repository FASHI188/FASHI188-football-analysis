# E3g-0B External PIT Source Feasibility Contract

## Status

- Research-only source screening.
- No data purchase or subscription.
- No credentialed API calls.
- No large historical downloads.
- No model fitting or candidate probabilities.
- No second-source ingestion.
- `formal_weight=0`.
- Draft only; do not merge automatically.

## Formal rule boundary

The sole CURRENT is V5.0.2. Pure 90-minute H/D/A research is isolated from exact score, total goals and BTTS. Those outputs and their protection gates are not applicable and cannot trigger Champion fallback.

## Frozen objects

The following remain unchanged:

- fixed 6,251 match identities and labels;
- fixed B100;
- market H/D/A probabilities;
- Champion;
- E3e through E3f-2A OOF predictions and baseline results;
- 90-minute including stoppage-time settlement convention.

## Scope

Screen external sources in this priority order:

1. timestamped historical market trajectories;
2. complete historical xG/event data;
3. historical lineups, injuries and suspensions.

For each source record:

- official source and documentation;
- free/paid and login requirements;
- league/season coverage;
- estimated and verified fixed-sample overlap;
- continuity and selection-bias risk;
- `observed_at` / `available_at`;
- revision mechanism;
- raw snapshot/hash capability;
- licence restrictions;
- PIT status and recommendation.

## PIT statuses

Allowed source-level statuses:

- `FORMAL_PIT_PILOT_CANDIDATE`
- `PIT_UNVERIFIED_RESEARCH_ONLY`
- `PIT_RECONSTRUCTED_RESEARCH_ONLY_CANDIDATE`
- `FORWARD_COLLECTION_ONLY`
- `PIT_IDENTITY_OR_TIMESTAMP_FAILED`

## Controlled research grade

`PIT_RECONSTRUCTED_RESEARCH_ONLY` may only be proposed when:

- the selected league/season history is complete and continuous;
- target-match data never enters its own input;
- only matches completed before target kickoff are used;
- revision risk is explicit;
- results are ineligible for promotion;
- `formal_weight=0`;
- true future forward data must validate any signal;
- actual training requires separate user approval.

## Asset protection

Required zero counts:

- candidate model fits;
- candidate probabilities;
- external records downloaded;
- credentialed API calls;
- subscriptions;
- CURRENT/model/data/config/formal-weight mutations;
- joint score matrix, score, total-goal and BTTS mutations;
- Actions permission changes;
- automatic commit/push.

## Stop condition

After writing and validating the feasibility matrix:

- identify the strongest timestamp source;
- identify the lowest-cost paid preflight;
- specify a free forward collection plan;
- specify required fields and minimum accumulation thresholds;
- stop and wait for user and Codex source selection.

Do not start E3g-1.
