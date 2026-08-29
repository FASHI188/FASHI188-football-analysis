# Football3 R43GOV0 Minimal Migration Plan

Status: research-only migration plan. No model invention, no CURRENT changes, no main merge, no formal release, no recomputation or parameter changes for sealed U0/Y0 predictions.

## Objective

Rebuild Football3 so that training, strict historical rolling OOS replay and future live inference use the same identity, point-in-time feature, assembly and score-matrix pipeline. Preserve V500 as the numerical reference baseline and preserve existing collection, lockbox, hashes and settlement ledgers.

The migration is successful only when the new plumbing can reproduce frozen baseline/component outputs before any new predictive claim is made.

## Target call chain

```text
FixtureRequest(fixture_identity, as_of)
  -> CanonicalIdentityResolver
  -> PITFeatureStore.read(fixture, as_of)
  -> FeatureAssembler
       -> FeatureActivationReceipt
  -> ScoreMatrixPipeline
       -> V500BaselineComponent
       -> optional frozen components, one at a time
  -> ProbabilityProjection / unique 1X2 argmax
  -> PredictionReceipt + hashes
  -> immutable prediction row
  -> UnifiedEvaluator (labels joined only after prediction freeze)
```

The call chain is identical for `train_dataset_generation`, `historical_replay` and `live_inference`; only the fixture iterator and label availability differ.

## Phase M1 — Canonical team identity resolver

Create one source-agnostic resolver with a stable canonical team key.

Required inputs:

- competition/provider namespace;
- source team ID when available;
- normalized source name only as supporting metadata;
- effective date/season when identity changes matter.

Required outputs:

- `canonical_team_id`;
- mapping method (`exact_source_id`, `pinned_crosswalk`, `manual_approved_alias`);
- mapping source/hash/version;
- ambiguity state.

Rules:

- Reuse strict one-to-one R43A2 crosswalk evidence where applicable.
- Never silently use fuzzy matching for a numerical prediction.
- Ambiguity fails closed and is visible in the activation receipt.
- Resolver must be used by fixture, lineup, player, coach and market adapters.
- M1 is plumbing-only: V500 numerical output must remain unchanged.

M1 gate:

- deterministic repeated resolution;
- no ambiguous mapping accepted as active;
- a fixed compatibility fixture set produces identical V500 score-matrix hashes before/after resolver insertion.

## Phase M2 — Point-in-time feature store

Add a shared PIT record contract. Do not rewrite source collectors in the first migration.

Minimum record fields:

```text
feature_family
entity_type
canonical_entity_id
fixture_id / competition_id
value payload
source_name
source_record_id
source_hash
observed_at
known_at
effective_at
expires_at (optional)
leakage_class
historical_use_allowed
adapter_version
```

Read contract:

```text
read(feature_family, fixture_id, as_of)
```

A record can be numerically returned only if:

- canonical identity resolved;
- `known_at <= as_of`;
- source leakage policy allows historical/live numerical use;
- no later observation is substituted for an earlier as-of request.

Initial source policy:

- V500 prior-match state: allowed.
- R43B0R1 lagged lineup history / P(start): allowed after adapter verification.
- retrospective availability-data injury/suspension rows: label/evaluation only, not numerical historical feature.
- R42H technical rows: blocked from formal historical PIT activation until row-level source-known timestamps are independently proven; prior-match ordering alone is insufficient for this migration contract.
- R43Q frozen market ledger: allowed for fixtures carrying genuine `source_observed_at_utc`/same-snapshot times; no claim of broad historical coverage.
- coach/fatigue/depth/specialist state: excluded unless a new governance-approved data source exists.

M2 gate:

- automated attempts to read `known_at > as_of` return a hard inactive reason;
- every returned numerical record carries source and lineage hashes;
- V500 compatibility outputs remain unchanged.

## Phase M3 — Unified feature assembler + activation receipt

Create one assembler used by all modes.

For every feature family it must explicitly output:

```text
recognized
pit_legal
assembled
numeric_effect
experiment_passed
inactive_reason
```

`numeric_effect` cannot be inferred from a row count. It is true only if the family is wired to a numerical input consumed by the active component pipeline.

For each prediction, write a deterministic activation receipt containing:

- fixture identity and `as_of`;
- canonical teams;
- all requested feature families;
- source/identity/PIT counts;
- active numerical feature names;
- feature-value hash;
- component input/output matrix hashes;
- fallback reasons;
- final score-matrix hash and unique 1X2 result.

Critical lineup rule:

- current state remains `probable_lineup_numeric_effect_enabled = false` until an approved numerical adapter is explicitly enabled and its receipt proves activation.
- R43B0R1 P(start) may first run in shadow mode and appear as `recognized=true, pit_legal=true, assembled=false, numeric_effect=false`.
- Do not use target confirmed XI as a historical feature.

M3 gate:

- 100% target-match receipts;
- exactly one final 1X2 prediction per target match;
- no receipt may say `numeric_effect=true` when the family did not alter a component input or component selection.

## Phase M4 — One orchestration path for training, rolling replay and live inference

Introduce a single engine entrypoint with three modes that share all numerical code.

Mode differences allowed:

- `dataset`: iterates historical fixtures and emits feature/label partitions; labels are attached only after the prediction row is frozen.
- `replay`: strict chronological OOS evaluator; no random split; no same-kickoff result update before all same-kickoff predictions.
- `live`: consumes current collectors/lockbox and emits immutable forward prediction events.

Mode differences not allowed:

- separate feature definitions;
- different team identity logic;
- different matrix component code;
- current-only fallbacks that cannot be represented in a receipt.

M4 equivalence gate:

- with only V500 enabled, new engine reproduces the frozen V500 reference on a predetermined compatibility corpus within exact/deterministic numerical tolerance;
- prediction coverage is 100%;
- existing lockbox/hash/settlement formats are preserved by adapters, not rewritten.

## Phase M5 — Extract existing R43Q/R/T/U/Y logic into score-matrix components

This is extraction, not redesign.

### Q — market baseline component

Input: a PIT-legal same-snapshot frozen 1X2 + AH + OU market record.

Output: normalized score matrix plus component receipt.

Rules:

- use frozen R43Q implementation and parameters;
- do not search thresholds/coverage/parameters on consumed rows;
- absence of a valid same-time market snapshot activates explicit fallback, never fabricated market values.

### R — football residual component

Extract the existing frozen football residual transform only after its exact implementation/hash is identified in the source lineage used by the intended stack. No new residual model is created in R43GOV0.

### T — dynamic state component

Extract the frozen R43T bivariate total/difference residual-state update as a pure transform.

Rules:

- preserve same-kickoff batching rule;
- preserve frozen parameters;
- no retuning on the 53 consumed scored rows.

### U — diagonal gain component

Extract exact R43U diagonal factor `1.25` as a pure matrix transform.

Rules:

- factor remains frozen;
- existing 53-row evidence is compatibility/development evidence only;
- sealed U0/Y0 predictions are never rerun or rewritten;
- future confirmation begins only on a new future batch after migration gates pass.

### Y — draw calibration component

Before extraction, locate and pin the exact R43Y implementation, source HEAD/blob hash, input matrix contract, output contract and sealed-forward relationship.

Rules:

- do not reconstruct from memory;
- do not read settled outcomes to change the sealed implementation;
- adapter must reproduce frozen artifact outputs before it can be enabled for future predictions.

M5 component gate for every component:

1. standalone pure-function wrapper created;
2. frozen source hash recorded;
3. artifact-level reproduction against existing frozen outputs;
4. activation receipt shows input/output matrix hashes;
5. no parameter change;
6. no CURRENT/main modification.

Components are enabled one at a time for new historical development blocks or new future batches. No multi-factor jump is permitted.

## Phase M6 — Unified training-set generator and evaluator

### Training-set generator

For every target fixture emit exactly one immutable pre-label row containing:

- fixture/canonical identities;
- prediction `as_of`;
- active feature values and hashes;
- score-matrix component chain and hashes;
- final 1X2 probabilities;
- feature activation receipt reference.

Then attach labels in a separate post-freeze step.

Hard requirements:

- 100% target competition/match coverage under the declared cohort;
- no abstention/selective reporting;
- no current XI/backfilled market/current injury snapshot used for an earlier historical as-of;
- same-kickoff fixtures predicted before same-kickoff labels can update states.

### Unified evaluator

Always report:

- count / coverage;
- Top1 hits and accuracy;
- LogLoss;
- multiclass Brier;
- RPS;
- natural draw Top1 count;
- natural draw Top1 hits;
- actual result distribution;
- chronological time blocks;
- per-component/feature activation coverage;
- PIT failure and identity failure counts.

No component can be promoted from a viewed historical block that was used for design/tuning. R43AA0's 9203 rows remain compatibility evidence after migration, not a fresh confirmatory test.

## Minimal code-change order

The first implementation PR/branch after this audit should change only plumbing, in this order:

1. `identity/` canonical team resolver + tests.
2. `pit/` record/read contracts + tests.
3. `assembly/` feature assembler + receipt schema + tests.
4. `pipeline/` common orchestration wrapper with V500-only component adapter.
5. compatibility workflow that runs V500 old vs new and fails on coverage/hash/probability mismatch.
6. only after equivalence: add Q adapter; then R; then T; then U; then located/pinned Y, each behind a disabled-by-default flag and its own artifact reproduction test.
7. only after component extraction: upgrade historical dataset generator and evaluator.

Do not begin by merging experiment branches wholesale. Import the smallest frozen functions/data contracts required, with source hashes and provenance.

## Non-negotiable locks

- `CURRENT`: untouched.
- `main`: untouched.
- sealed 41-match U1/Y0 forward predictions: untouched; no recompute, no parameter change, no outcome-driven adjustment.
- existing collectors: retained initially.
- lockbox, hash chain and settlement chain: retained.
- V500: retained as baseline.
- failed fatigue, ordinary coach, specialist-state and similar failed mechanisms: not migrated without genuinely new data.
- retrospective availability labels: never treated as historical prematch known-at features.

## First implementation acceptance criteria

Before any predictive experiment resumes, the plumbing migration must prove:

- one canonical team identity path;
- one PIT read path;
- one assembler;
- one activation receipt per match;
- one shared numerical inference function for historical replay and live inference;
- V500 old/new numerical compatibility;
- 100% declared match coverage with unique 1X2 output;
- zero reads/writes of sealed U0/Y0 prediction contents during compatibility testing.

Only after these criteria pass should the research program resume one-component-at-a-time ablations on new, governance-valid time blocks.