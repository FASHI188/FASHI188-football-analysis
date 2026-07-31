# E3g-0D API-Football Forward Collection Safety Contract

- contract_version: `E3G0D-CONTRACT-1.3`
- CURRENT: `V5.0.2`
- formal_weight: `0`
- base_head: `9376cbb2c6af6945a4a1be709b606d928c28a826`
- deployment_status: `IMPLEMENTED_NOT_LIVE`
- remediation_status: `GPT_REMEDIATED_PENDING_CODEX_RECHECK`
- live_api_calls_in_this_change: `0`

## Stop boundary

This change repairs only five code-proven PR #75 deployment blockers on the Codex-rechecked HEAD. It does not create or read `API_FOOTBALL_KEY`, call API-Football, enable collection or schedule, merge the PR, train a model, produce candidate probabilities, start E3g-1, or change formal model/data/config/CURRENT/weights, score matrix, score module, total-goal module, or BTTS module.

Defaults remain `API_FOOTBALL_COLLECTOR_ENABLED=false`, `API_FOOTBALL_SCHEDULE_ENABLED=false`, `IMPLEMENTED_NOT_LIVE`, and `formal_weight=0`.

## 1. Workflow expression and shell boundary

Untrusted `workflow_dispatch` inputs and prior-step outputs must never be interpolated directly into a `run:` shell body or Python here-document. GitHub expressions may enter a step only through declarative `env:`, `if:`, or action `with:` fields. Shell and Python then read environment variables, validate type and syntax, reject CR/LF/NUL, and pass values through quoted argument arrays.

The credentialed collector step follows the same rule. Its single Secret reference is an `env:` mapping on the guarded main-only step. No input or step output is executable shell source. Validation must prove `direct_inputs_in_run=0` and `direct_step_outputs_in_run=0`.

## 2. Immutable third-party Actions

Every external `actions/*` use is pinned to a full 40-character commit SHA. Mutable version tags such as `@v4`, `@v5`, branches, or floating releases are forbidden. Checkout remains `persist-credentials: false`; top-level permissions remain `contents: read`, and only the live/status job adds `actions: read`.

## 3. Durable pre-request quota reservation

Quota safety no longer depends on a post-request Artifact upload. Before the credentialed collector can run, the serialized live job must:

1. read all same-day reservation Artifacts;
2. validate every mandatory GitHub digest;
3. reserve the run's full `max_requests` envelope;
4. write `quota_reservation.json` append-only;
5. upload a 30-day reservation Artifact successfully.

The collector step is gated on successful reservation upload. The ledger counts conservative pre-request reservations rather than optimistic post-run actuals. Therefore a later collector crash, timeout, validation failure, or post-run audit upload failure cannot make already-authorized capacity disappear. Duplicate reservation IDs are deduplicated only when byte-equivalent; conflicts fail closed. Total durable reservations above 90 make quota state untrusted.

A post-run quota audit may record actual attempts, but it is not used to reduce or replace the durable reservation. This intentionally over-reserves on failed or partially used runs and is the safe behavior.

## 4. Mandatory Artifact digest

Artifact digest is mandatory for every downloaded E3g-0D quota reservation, daily plan, plan-index, archive candidate, and other evidence used by a safety decision. Missing, malformed, or mismatched GitHub `sha256:` metadata fails closed. ZIP CRC and internal raw-response SHA chains remain additional checks, not substitutes for the outer Artifact digest.

## 5. Plan-index Artifact is authoritative

A daily plan Artifact is not sufficient by itself. Plan-dependent collection must also locate exactly one non-expired `football-e3g0d-plan-index-<plan_artifact_id>-...` Artifact, require its digest, and verify that it binds:

- plan Artifact ID;
- exact plan Artifact name;
- plan Artifact digest;
- canonical plan SHA-256;
- source raw response SHA-256;
- run HEAD;
- workflow run ID;
- request day and target date;
- competition and season;
- retention, append-only, and `formal_weight=0`.

The resolver writes one append-only `selected_plan_identity.json`. The credentialed collector accepts only this single verified identity file, not a set of independently supplied step outputs. Records, manifests, and run receipts preserve plan Artifact ID/digest, plan-index Artifact ID/digest, plan SHA, and source raw SHA. Missing or inconsistent plan-index evidence fails closed.

## Retained safety controls

- Only `https://v3.football.api-sports.io` and endpoints `fixtures`, `odds`, `injuries`, `fixtures/lineups` are allowed.
- The supported `injuries?ids=fixture1-fixture2` batch remains valid.
- Redirects are refused.
- Timeout 15s/default and 30s/hard; retry 1/default and 2/hard; backoff 8s/default and 30s/hard; response 10 MiB; per-run 20; free daily 100; project cap 90.
- `request_day_utc` is the actual UTC request day; `target_date_utc` is only the fixture date.
- Every attempt and retry is counted before transport, with UTC rollover fail-closed.
- Run success/failure receipts remain append-only and sanitized.
- Complete Artifact/workflow-run pagination uses `per_page=100`, deduplicates IDs, and fails closed on interruption or malformed data.
- Every E3g-0D Artifact uses `retention-days: 30`; recommended manual archive interval is 21–28 days and hard maximum 30 days.
- Raw responses remain content-addressed; observations, manifests, records, archives, and final-freeze markers remain append-only.
- Empty lists remain `MISSING_UNINTERPRETED`; post-kickoff data cannot replace pre-kickoff evidence.
- Online T-15 records are candidates only; the local post-kickoff finalizer writes a separate marker.
- No repository write permission, commit, push, automatic PR, direct-main write, Artifact deletion, or raw API payload commit is allowed.

## Required no-key validation

Draft-PR validation runs with no API-Football key and no provider network. It must prove compilation, no direct expressions in `run:`, full-SHA action pinning, Secret isolation, actual request-day isolation, durable quota reservation before provider access, reservation upload gating, failure receipt persistence, fixed live concurrency, complete pagination, mandatory Artifact digest, plan-index enforcement, 30-day retention, append-only/raw SHA/empty semantics/final-freeze, exact formal asset diff zero, live job skipped, and real API calls zero.

A validation PASS is not live collection success and is not Codex acceptance. After GPT remediation the only allowed state is `GPT_REMEDIATED_PENDING_CODEX_RECHECK`. PR #75 remains Open, Draft, unmerged and stopped for Codex recheck of the exact new HEAD.

Validation markers: pre-request-reservation; full commit SHA; plan-index Artifact; Artifact digest is mandatory; no credentialed API call; do not start E3g-1.
