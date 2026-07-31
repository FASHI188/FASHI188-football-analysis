# E3g-0C Zero-Paid-Request Budget and Free Forward Collection Design Contract

## Status

- research-only design;
- no purchase or subscription;
- no credentialed API call;
- no API key or GitHub Secret creation;
- no scheduled job deployment;
- no candidate model fit or probability;
- `formal_weight=0`;
- Draft only; do not merge automatically.

## Frozen research objects

- unique CURRENT V5.0.2 pure H/D/A isolation remains controlling;
- fixed 6,251 match identities and labels;
- fixed B100 and all registered OOF baselines;
- no modification of the existing 93 internal features;
- 90 minutes including stoppage time only.

## The Odds API budget audit

The calculation must use the fixed schedule and the exact formula:

`historical credits = 10 * number_of_regions * number_of_markets * number_of_independent_query_timestamps`

The selected league-season is chosen by identity and kickoff-time completeness, not model performance. Ties are resolved using official public sample support and a season fully inside the five-minute historical-snapshot era.

Required target timestamps per match:

- T-72h;
- T-24h;
- T-6h;
- T-90m;
- T-15m.

Queries are deduplicated across matches and offsets. The audit reports raw credits plus separate 10% retry and 5% identity/time-alignment reserves.

No request may be sent to a credentialed or paid endpoint. Only the provider's no-key public sample files may be downloaded for schema validation.

## Public sample validation

The public samples must demonstrate:

- `timestamp`;
- `previous_timestamp`;
- `next_timestamp`;
- event `commence_time`;
- bookmaker `last_update`;
- soccer h2h includes `Draw`;
- spreads and totals outcomes include `point`.

The workflow records source URL, byte size and SHA-256, but does not commit the sample files.

## API-Football free forward design

Use the selected league's busiest fixed-schedule day and the free quota of 100 requests/day.

Calculate:

- one league/date fixtures request;
- odds every three hours, batched by league/date and paginated at ten results/page;
- injuries every four hours, batched where coverage permits;
- lineups per fixture from T-120 to T-15 every fifteen minutes;
- naive per-fixture design;
- priority-reduced design with final freeze, coarse odds, injuries, and T-90/T-45/T-15 lineups;
- failure-retry reserve.

The design is documentation only. It creates no secret, cron, workflow schedule or external collection.

## Security controls

- API keys only in GitHub Secrets or local environment variables;
- keys forbidden in repository, logs and Artifacts;
- raw provider responses append-only;
- every observation records `observed_at_utc`, provider update time, request parameters, HTTP status and SHA-256;
- final pre-kickoff version frozen separately;
- post-match backfill never overwrites pre-match missing state;
- GitHub Actions permissions remain `contents: read`;
- no automatic commit or push.

## Formal asset protection

The branch may add only:

1. this contract;
2. the budget/design script;
3. a read-only validation workflow.

Changes to model, data, config, CURRENT, formal weights, unified score matrix, score, total-goal or BTTS modules are forbidden.

## Stop condition

After producing the exact budget, public-sample schema audit, free-forward daily quota design, HEAD/run/job/Artifact receipt and asset-diff check, stop. Do not start E3g-1.
