# E3g-0D API-Football Forward Collection Safety Contract

- contract_version: `E3G0D-CONTRACT-1.1`
- CURRENT: `V5.0.2`
- research_scope: pure 90-minute H/D/A support data only
- formal_weight: `0`
- base_head: `9376cbb2c6af6945a4a1be709b606d928c28a826`
- deployment_status: `IMPLEMENTED_NOT_LIVE`
- live_api_calls_in_this_change: `0`

## 1. Purpose and stop point

This contract governs the one-league API-Football forward PIT collector before any live activation. The implementation may be reviewed, self-tested, and merged later, but implementation or merge does not constitute activation.

This stage does not:

- create an API account;
- create or request a Secret;
- perform a credentialed request;
- start scheduled collection;
- train a model;
- produce candidate probabilities;
- start E3g-1;
- change any formal model, formal data, formal configuration, CURRENT, score matrix, score module, total-goal module, BTTS module, or formal weight.

The only permitted deployment status by default is `IMPLEMENTED_NOT_LIVE`.

## 2. Explicit activation and schedule boundary

GitHub scheduled workflows run only from the repository default branch. A Draft PR or a non-default research branch cannot be represented as a durable scheduled deployment.

Two independent repository variables gate live execution:

- `API_FOOTBALL_COLLECTOR_ENABLED=false`
- `API_FOOTBALL_SCHEDULE_ENABLED=false`

An absent variable is interpreted as `false`.

A merge into `main` does not change either variable and therefore cannot automatically change the status to LIVE. Credentialed provider access is permitted only when all applicable controls are true:

1. the workflow ref is exactly `refs/heads/main`;
2. the trigger is controlled `workflow_dispatch` or `schedule`;
3. `API_FOOTBALL_COLLECTOR_ENABLED` is explicitly `true`;
4. `dry_run=false` and `no_network=false`;
5. for a scheduled run, `API_FOOTBALL_SCHEDULE_ENABLED=true` and schedule use is explicitly allowed;
6. the API key exists in the approved environment source;
7. the per-run and daily request budgets pass before the first request.

If any control is absent, false, malformed, or inconsistent, the run must remain no-network or fail closed. There is no fallback that silently enables collection.

## 3. Secret contract

`API_FOOTBALL_KEY` may come only from:

- a GitHub Actions repository Secret referenced in the guarded live step; or
- a local environment variable.

It must not be:

- written into source code;
- written into configuration;
- accepted as a command-line argument;
- printed to stdout or stderr;
- included in an exception message or traceback produced by the collector;
- included in a URL or request parameter;
- included in a raw provider response stored by the collector;
- included in a manifest, receipt, Artifact, cache, or test fixture;
- automatically created;
- requested from the user in chat.

The collector rejects any credential-like request parameter and rejects a provider payload containing the key bytes before storage. Provider error bodies are not interpolated into exception messages.

Pull-request and fork-triggered jobs do not reference or receive `API_FOOTBALL_KEY`. The workflow does not use `pull_request_target`. The sole Secret reference is attached to the guarded live collector step, which requires the exact main-branch live guard.

## 4. Workflow permissions and repository protection

Top-level workflow permission is:

```yaml
permissions:
  contents: read
```

The live/status job may additionally request `actions: read` solely to list and download existing Artifacts and quota receipts. No write permission is granted.

Forbidden capabilities include:

- `contents: write`;
- `actions: write`;
- `pull-requests: write`;
- automatic commit or push;
- automatic PR creation;
- a repository persistence helper;
- direct main writes;
- deleting GitHub Artifacts.

`actions/upload-artifact` is temporary workflow storage and does not grant repository write permission.

## 5. Provider access boundary

The only permitted provider base is:

- scheme: `https`;
- host: `v3.football.api-sports.io`;
- base URL: `https://v3.football.api-sports.io`.

Allowed endpoint types are limited to:

- `fixtures`;
- `odds`;
- `injuries`;
- `fixtures/lineups`.

Unknown endpoints, non-HTTPS URLs, and redirects are rejected. Redirects are not followed to any other host.

Default and hard limits are:

- request timeout: `15` seconds; hard maximum `30` seconds;
- retry count: `1`; hard maximum `2`;
- exponential backoff cap: `8` seconds; hard maximum `30` seconds;
- first-trial per-run request attempts: `3`;
- hard per-run request attempts: `20`;
- provider free daily limit: `100`;
- project daily safety cap: `90`, reserving ten requests;
- maximum response body: `10 MiB`.

Every request attempt, including a retry, consumes the run budget. Prior same-day quota receipt Artifacts are summed before a live run. A run is rejected if the projected count exceeds the safety cap. Provider rate-limit headers are also checked and collection stops when the reserve is reached.

Response handling is fail closed:

- `429`: retry only within the bounded retry and `Retry-After`/backoff limits;
- `5xx`: retry only within the bounded retry and backoff limits;
- other HTTP failures: stop;
- non-JSON, oversized, non-object JSON, or provider error object: stop;
- network timeout or transport failure: bounded retry, then stop;
- identity mapping failure: stop;
- competition/season mismatch: stop;
- missing fixture/team identity: stop;
- abnormal or out-of-date kickoff timestamp: stop;
- missing daily plan for a plan-dependent mode: stop without provider fallback.

There is no unlimited retry path.

## 6. First live trial envelope

The manual workflow inputs independently expose:

- one approved league (`league_id=39` only);
- one UTC date;
- a fixture limit;
- a maximum request-attempt limit;
- `dry_run`;
- `no_network`;
- `upload_artifact`;
- `allow_schedule`.

Defaults are deliberately inactive:

- mode: `preflight`;
- league: `39`;
- season: `2026`;
- fixture limit: `1`;
- maximum request attempts: `3`;
- `dry_run=true`;
- `no_network=true`;
- `upload_artifact=false`;
- `allow_schedule=false`.

A first live trial must remain one league, one date, one fixture, and no more than three request attempts unless a later reviewed change explicitly alters that envelope. It must not automatically expand to the full Premier League season or start a recurring loop.

## 7. PIT identity and append-only evidence

Every fixture-level PIT record contains these fields, even when a provider field is unavailable and therefore null:

- `provider`;
- `competition_id`;
- `season_id`;
- `fixture_id`;
- `home_team_id`;
- `away_team_id`;
- `scheduled_kickoff_utc`;
- `kickoff_version_id`;
- `provider_updated_at`;
- `observed_at_utc`;
- `requested_at_utc`;
- `request_endpoint_type`;
- `raw_response_sha256`;
- `run_head`;
- `workflow_run_id`;
- `data_status`;
- `missing_reason`;
- `is_pre_kickoff`;
- `is_final_pre_kickoff_candidate`;
- `is_final_pre_kickoff_freeze_version`;
- `formal_weight=0`.

Raw provider bodies are content-addressed by SHA-256. An identical response may reuse the immutable raw blob after byte-for-byte verification, while every observation receives a new append-only manifest and fixture-level record. Existing evidence is never overwritten.

The following interpretation rules are mandatory:

- an observation at or after kickoff cannot replace or relabel an earlier pre-kickoff observation;
- a lineup added after the match cannot be represented as a pre-match lineup;
- an empty provider list is `MISSING_UNINTERPRETED`, not “no injuries” or “no lineup”;
- kickoff changes create a new `kickoff_version_id` and preserve earlier versions;
- online T-15 collection may mark only `is_final_pre_kickoff_candidate=true`;
- online collection must not claim that a record is the final freeze version;
- after kickoff, the local archive finalizer selects the latest `observed_at_utc` strictly before the same kickoff version and writes a separate append-only final-freeze marker with `is_final_pre_kickoff_freeze_version=true`.

This post-kickoff selection does not modify the original record, ZIP, Artifact, or repository.

## 8. Artifact retention and local archive

GitHub Artifacts are temporary transport, not the long-term datastore.

Configured retention is:

- validation Artifact: `30` days;
- no-network/manual snapshot Artifact: `30` days;
- live snapshot Artifact: `30` days;
- daily fixture-plan Artifact: `2` days;
- daily quota-receipt Artifact: `2` days.

Each generated output records `retention_days` and an expected `expires_at_utc`. The read-only archive helper also reads GitHub's actual Artifact `expires_at` metadata and GitHub-provided SHA-256 digest.

The maximum archival interval is 30 days. A local archive cycle must occur no less frequently than every 30 days.

Before accepting an archive copy, the helper must:

1. list Artifacts not present in the local append-only archive manifest;
2. download a selected Artifact only when invoked manually;
3. compare the downloaded ZIP SHA-256 with GitHub Artifact metadata;
4. test ZIP integrity;
5. verify each available raw-response SHA-256 link in snapshot manifests;
6. write the ZIP under a content-addressed local path;
7. append a local archive manifest row;
8. leave the GitHub Artifact unchanged;
9. leave the repository unchanged.

The helper provides these manual commands:

- `list-unarchived`;
- `download --artifact-id ...`;
- `finalize-freeze`;
- `status`;
- `self-test`.

It does not auto-run and this implementation-validation round does not download a live Artifact. Large raw API payloads must not be committed into the repository.

## 9. Schedule inactivity and expiry monitoring

Public-repository schedules may become inactive after prolonged repository inactivity. The project must not use meaningless commits to keep a workflow active.

The read-only `status` command reports:

- workflow file and workflow id when present on the default branch;
- workflow state;
- latest expected schedule time derived from the registered cron set;
- latest actual schedule-triggered run time;
- latest actual workflow run time;
- a possible missed-run flag;
- Artifacts within seven days of expiry;
- that meaningless keepalive commits are forbidden.

The check uses GitHub read APIs only. It does not reactivate a workflow, edit a workflow, delete an Artifact, commit, push, or open a PR.

## 10. Validation requirements

Draft-PR validation must run with no API-Football key and no provider network access. It must prove:

- both Python files compile;
- default activation and schedule switches are false;
- preflight remains `IMPLEMENTED_NOT_LIVE`;
- Secret material is not persisted;
- host and endpoint allowlists work;
- request and daily budget guards work;
- append-only evidence and raw deduplication work;
- empty response semantics remain missing/uninterpreted;
- online collection does not mark a final freeze version;
- the local post-kickoff finalizer selects the latest valid pre-kickoff version;
- local archive manifests are append-only;
- no GitHub Artifact or repository mutation occurs in helper self-tests;
- candidate probabilities = 0;
- model fits = 0;
- formal asset changes = 0.

## 11. Required completion state

After this safety-remediation round:

- keep PR #75 Draft and unmerged;
- keep `formal_weight=0`;
- keep `API_FOOTBALL_COLLECTOR_ENABLED=false`;
- keep `API_FOOTBALL_SCHEDULE_ENABLED=false`;
- report the exact new HEAD and exact validation run/job/Artifact/digest;
- report live API calls separately and keep them at zero;
- no credentialed API call;
- no automatic commit from a workflow;
- do not start E3g-1;
- stop for independent Codex review.
