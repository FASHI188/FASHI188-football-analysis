# E3g-0D API-Football Free Forward Collection Contract

- contract_version: `E3G0D-CONTRACT-1.0`
- CURRENT: `V5.0.2`
- research_scope: pure 90-minute H/D/A support data only
- formal_weight: `0`
- base_head: `9376cbb2c6af6945a4a1be709b606d928c28a826`
- status: `IMPLEMENTED_NOT_LIVE`

## 1. Purpose

Implement a one-league API-Football forward collector for genuine pre-match `observed_at_utc` snapshots. This stage does not train a model, produce candidate probabilities, or start E3g-1.

The initial pilot identity is:

- provider: API-Football v3;
- league: English Premier League;
- API league id: `39`;
- API season: `2026` (2026/27 season start year);
- timezone: UTC.

## 2. Activation boundary

Implementation approval does not imply that live collection has started.

Live collection requires all of the following:

1. this Draft PR is reviewed and intentionally integrated into a branch on which the workflow can run;
2. the user supplies an API-Football key outside the repository;
3. the key is saved only as GitHub Secret `API_FOOTBALL_KEY` or a local environment variable;
4. a durable archive decision is recorded before the 90-day GitHub Artifact retention window becomes a data-loss risk.

Until those conditions are met:

- no credentialed API call;
- no Secret creation;
- no scheduled collection claim;
- no candidate probability;
- no model fit;
- no E3g-1.

## 3. Official endpoint assumptions

The implementation follows current official API-Football documentation:

- API base: `https://v3.football.api-sports.io/`;
- authentication header: `x-apisports-key`;
- Premier League id: `39`;
- season value is the starting year;
- fixtures can be filtered by league, season and date;
- pre-match odds are updated approximately every three hours and retain about seven days of history;
- injuries are updated approximately every four hours and accept up to twenty fixture ids through `ids`;
- lineups normally appear shortly before kickoff, but may be missing pre-match and added after the match;
- coverage flags do not guarantee complete match-level availability.

## 4. Collection schedule design

After intentional activation on a runnable branch:

- fixture plan: once daily at 00:05 UTC;
- odds batch: every three hours;
- injuries batch: every four hours;
- lineup window evaluator: every fifteen minutes from 10:00 through 22:45 UTC;
- lineup snapshots: T-90m, T-45m and T-15m;
- exact odds snapshots: T-90m and T-15m;
- final injury snapshot: T-15m.

The lineup evaluator downloads the immutable daily fixture-plan Artifact through read-only GitHub Actions access. It must not spend API-Football quota merely to rediscover the schedule every fifteen minutes.

If the plan Artifact is missing or stale, lineup and final-freeze collection must skip rather than issue an unbudgeted fallback request.

## 5. Snapshot contract

Every provider response must be stored as an immutable raw JSON file plus a sidecar manifest containing:

- provider;
- endpoint;
- sanitized request parameters;
- `requested_at_utc`;
- `observed_at_utc`;
- safe rate-limit headers;
- HTTP status;
- raw payload SHA-256;
- related fixture ids and kickoffs;
- target label;
- whether it is a final pre-kickoff candidate;
- `formal_weight=0`.

Files use exclusive-create semantics. Existing files may never be overwritten.

The API key must never appear in:

- repository content;
- request parameters;
- stdout or stderr;
- workflow logs;
- manifests;
- raw payloads;
- Artifacts.

## 6. PIT and freeze rules

- only observations with `observed_at_utc < kickoff_utc` are pre-match candidates;
- T-15m observations are marked as final pre-kickoff candidates;
- downstream use must select the latest observation strictly before kickoff;
- a post-match lineup or injury update may be stored as a later observation but may not overwrite or relabel a pre-match missing state;
- missing pre-match data is itself a preserved PIT fact;
- target-match events, statistics and outcomes are never collected as predictive inputs in this stage.

## 7. Storage limitation

GitHub Actions Artifacts are temporary transport, not yet an approved long-term research datastore.

- plan Artifacts may use short retention;
- snapshot Artifacts may use the repository maximum retention, currently designed as 90 days;
- 90 days is insufficient to accumulate 300–500 Premier League matches;
- before live activation, the project must approve either periodic manual archival or another append-only durable store;
- no automatic commit or push may be introduced to solve storage.

Therefore the current deployment status remains `IMPLEMENTED_NOT_LIVE` even after code validation.

## 8. Permissions and repository protection

- workflow permissions: `contents: read`, `actions: read` only;
- no automatic commit;
- no automatic push;
- no PR merge;
- no Actions permission expansion;
- no modification to model, formal data, config, CURRENT, score matrix, score module, total-goal module, BTTS module or formal weights.

## 9. Validation requirements

PR validation must run without an API key and prove:

- Python syntax;
- append-only exclusive writes;
- raw payload and manifest SHA linkage;
- secret redaction;
- fixture-plan parsing;
- T-90/T-45/T-15 due-window selection;
- final freeze-candidate creation;
- candidate probabilities = 0;
- model fits = 0;
- formal asset changes = 0.

## 10. Stop point

After implementation validation:

- keep the PR Draft;
- report exact HEAD, run, job and Artifact;
- report Secret status and live-collection status separately;
- do not create a Secret;
- do not merge;
- do not start E3g-1;
- wait for the user and Codex to choose activation and durable storage.
