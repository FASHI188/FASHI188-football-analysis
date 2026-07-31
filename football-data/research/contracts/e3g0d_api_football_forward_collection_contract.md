# E3g-0D API-Football Forward Collection Safety Contract

- contract_version: `E3G0D-CONTRACT-1.2`
- CURRENT: `V5.0.2`
- formal_weight: `0`
- base_head: `9376cbb2c6af6945a4a1be709b606d928c28a826`
- deployment_status: `IMPLEMENTED_NOT_LIVE`
- remediation_status: `GPT_REMEDIATED_PENDING_CODEX_RECHECK`
- live_api_calls_in_this_change: `0`

## Stop boundary

This change repairs only PR #75 E3g-0D collection safety. It does not create/read `API_FOOTBALL_KEY`, call API-Football, enable collection or schedule, merge the PR, train a model, produce candidate probabilities, start E3g-1, or change formal model/data/config/CURRENT/weights, score matrix, score module, total-goal module, or BTTS module.

Defaults remain:

- `API_FOOTBALL_COLLECTOR_ENABLED=false`
- `API_FOOTBALL_SCHEDULE_ENABLED=false`
- `deployment_status=IMPLEMENTED_NOT_LIVE`
- `formal_weight=0`

A merge never activates collection. PR/fork jobs cannot receive the Secret; `pull_request_target` is forbidden. The key may come only from a guarded main-branch repository Secret or a local environment variable, never CLI/source/config/URL/log/exception/raw evidence/manifest/receipt/Artifact/cache/test data/chat.

## Permissions and provider boundary

Top-level permissions are `contents: read`; the live/status job may add only `actions: read`. Checkout uses `persist-credentials: false`. Repository writes, `contents: write`, `actions: write`, `pull-requests: write`, commit, push, automatic PR, direct-main writes, Artifact deletion, and repository persistence helpers are forbidden.

Only `https://v3.football.api-sports.io` and endpoints `fixtures`, `odds`, `injuries`, `fixtures/lineups` are allowed. Redirects are refused. The supported `injuries?ids=fixture1-fixture2` batch remains valid. Limits remain: timeout 15s/default and 30s/hard; retry 1/default and 2/hard; backoff 8s/default and 30s/hard; response 10 MiB; per-run 20; free daily 100; project cap 90.

## Actual UTC request-day quota

`request_day_utc` is the UTC natural day on which an API attempt actually occurs. `target_date_utc` is only the fixture date. They are separate and may differ.

All quota lookup, Artifact naming, accumulation, free-limit protection, and the 90-attempt safety cap use `request_day_utc`. User-selected target dates cannot alter quota ownership. The collector checks the UTC day before each attempt and fails closed on rollover or untrusted quota state. Every retry increments the budget before transport.

## Mandatory success/failure receipts

The collector writes one append-only sanitized run receipt in a `finally` path. Success and failure receipts include schema, deployment status, outcome, mode, request day, target date, request attempts, prior daily usage, limits, HEAD, workflow run ID, observation time, retention, append-only, and `formal_weight=0`.

Failures record only a stable class such as `NETWORK_FAILURE`, `HTTP_429`, `HTTP_5XX`, `PROVIDER_ERROR`, `NON_JSON_RESPONSE`, `RESPONSE_TOO_LARGE`, `PROVIDER_QUOTA_RESERVE_REACHED`, `IDENTITY_MAPPING_FAILED`, `APPEND_ONLY_WRITE_FAILED`, or `VALIDATION_FAILED`. They never include credentials, provider bodies, authorization headers, sensitive URLs, or tracebacks.

Workflow quota preparation/upload uses explicit `always()` conditions. If attempts occurred, the quota receipt must upload even when the collector fails. The final gate preserves collector failure. Missing/unwritable/unuploadable quota evidence makes quota state untrusted and fails closed.

## Fixed live concurrency

All main `workflow_dispatch` and enabled `schedule` live runs share:

```yaml
concurrency:
  group: e3g0d-api-football-live-main
  cancel-in-progress: false
```

The group contains no run ID, date, mode, or fixture. Quota read, provider attempts, run/quota receipt writing, and quota upload remain in this serialized job. PR validation is separate.

## Complete Artifact pagination

All Artifact/workflow-run listings read `per_page=100` and continue page 1, 2, 3, and onward until a short page. IDs are deduplicated. HTTP, payload, row, or pagination failure is fatal and cannot mean no Artifact, no plan, or `used=0`.

This applies to quota receipts, plan resolution, `GitHubReader.artifacts()`, unarchived lists, expiry monitoring, archive candidates, and schedule status.

## Uniform retention and archive

Every E3g-0D Artifact uses `retention-days: 30`: validation, no-network, live snapshots, daily fixture plans, plan-index receipts, and daily quota receipts. Generated metadata uses `retention_days=30` and expected expiry +30 days; GitHub `expires_at` remains authoritative.

Recommended manual archive interval is 21–28 days; hard maximum is 30 days. Archive helpers remain read-only, append-only, verify GitHub digest/ZIP CRC/raw SHA chains, never delete Artifacts, never modify the repository, and never commit raw API data.

## Immutable daily plan identity

Each plan computes canonical `plan_sha256`. Its Artifact name includes target UTC date, league ID, season ID, and plan SHA. Plan content records plan SHA, source raw SHA, source manifest, request day, target date, HEAD, run ID, competition, season, fixture count, creation time, append-only, and `formal_weight=0`.

After upload, an append-only plan-index receipt binds the assigned Artifact ID to plan SHA and source raw SHA. Plan-dependent collection must explicitly resolve one identity and verify Artifact ID, plan SHA, target date, league, season, run HEAD, source manifest, and source raw SHA before provider access. Multiple unpinned candidates fail closed; no newest-plan fallback is allowed. Snapshots/manifests/run receipts record selected plan Artifact ID, selected plan SHA, and selected source raw SHA.

## PIT controls retained

Raw responses remain SHA-256 content-addressed; observations/manifests/records remain append-only. Empty lists remain `MISSING_UNINTERPRETED`. Post-kickoff data cannot replace pre-kickoff evidence. Online T-15 records are candidates only. The local post-kickoff finalizer writes a separate append-only marker selecting the latest observation strictly before the same kickoff version.

## Required no-key validation

Draft-PR validation uses no key and no provider network. It must prove: compilation; Secret/static workflow safety; request-day/target-date isolation; cap bypass prevention; failed/retried/post-validation attempt persistence; failed job preservation; fixed shared concurrency; 250-Artifact full pagination and fail-closed interruption; all retention 30 days; immutable plan SHA/Artifact/source chain; multiple unpinned plan refusal; snapshot plan identity; append-only/raw SHA/empty semantics/final-freeze; exact formal asset diff zero; live job skipped; real API calls zero.

A simulated PASS is not a live collection success. After GPT remediation the only allowed status is `GPT_REMEDIATED_PENDING_CODEX_RECHECK`. PR #75 remains Draft, open, unmerged, `IMPLEMENTED_NOT_LIVE`, and stopped pending Codex recheck of the exact new HEAD.

Validation marker: no credentialed API call; do not start E3g-1.
