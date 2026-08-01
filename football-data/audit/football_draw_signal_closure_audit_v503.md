# Football Draw Signal Closure Audit V5.0.3

- Version: V5.0.3
- Status: CURRENT_AUTHORITATIVE_RULE
- Previous version: V5.0.2, retained in Git history and existing `v502` implementation files as HISTORICAL_VALIDATED_VERSION
- Scope: read-only draw-signal closure audit and its outward claims
- Formal weight: 0
- Training authorization: false
- New target-period scoring authorization: false
- Provider/network authorization: false

This file is the single authoritative normative rule for Football Draw Signal Closure Audit V5.0.3. Implementations, tests, workflows, PR receipts, Artifact summaries, and handoff reports MUST reference this path and MUST NOT restate a conflicting full rule elsewhere.

## 1. Preserved V5.0.2 boundaries

V5.0.3 preserves the validated V5.0.2 technical boundaries unless this document explicitly tightens them:

- frozen expected asset registry and independently discovered live Git actual asset universe;
- explicit field data-flow contracts and fail-closed prior-use classification;
- canonical route closure participation in the final decision;
- separate global, domain-specific strict-PIT, and reconstructed-research candidate scopes;
- retrospective market aliases are not prediction-time evidence;
- `formal_weight=0`;
- `model_training=0`;
- `new_target_period_scoring=0`;
- `provider_network_used=false`;
- `external_request_attempts=0`;
- `api_football_key_accessed=false`;
- no formal model, formal data, formal configuration, CURRENT, or weight modification.

V5.0.2 did not contain the claim-boundary contract below. V5.0.3 results MUST NOT be attributed to a V5.0.2 HEAD or Artifact.

# Evidence Classification and Claim Boundary Contract

This section is normative. Every outward claim MUST have exactly one evidence status and MUST remain inside its recorded scope.

## 2. Evidence statuses

### 2.1 PROVEN

`PROVEN` means direct, independently reviewable evidence corresponds to the exact claim. The claim record MUST include:

- claim subject;
- exact scope;
- exact HEAD;
- evidence file or Artifact reference;
- evidence generation method;
- time boundary;
- whether the production path actually executed;
- whether any path was skipped;
- whether mock or simulation was used.

`PROVEN` MUST NOT exceed the evidence scope.

### 2.2 COMPUTED

`COMPUTED` means identified inputs were processed by a deterministic program. The claim record MUST include:

- input identity;
- input scope;
- algorithm or script identity;
- exact script HEAD or SHA-256;
- output identity;
- reproducibility status;
- an explicit statement that computation does not imply business validity.

Coverage, sample counts, hashes, distributions, route counts, and test counts are normally `COMPUTED`, not business-level `PROVEN` conclusions.

### 2.3 INFERRED

`INFERRED` means an interpretation, candidate judgment, or research judgment based on evidence. It MUST carry one structured qualifier from:

- 提示
- 可能
- 候选
- 值得预注册验证
- 尚需实验
- 当前证据倾向于

An `INFERRED` claim MUST NOT be represented as proven, confirmed effective, formally usable, solved, or exhausted.

### 2.4 UNPROVEN

`UNPROVEN` is mandatory when evidence is insufficient or a required gate is open, including:

- incomplete data-flow evidence;
- unproved PIT or `available_at` boundary;
- holdout not proved untouched;
- possible target-period exposure;
- any `UNRESOLVED` route;
- missing result evidence;
- unexecuted production path;
- mock-only execution;
- workflow Success with a critical job skipped;
- coverage without predictive-gain experiment;
- preregistration without an executed result.

### 2.5 NOT_AUTHORIZED

`NOT_AUTHORIZED` means an action may be technically possible but lacks separate explicit user authorization. Until separately approved, this status is mandatory for:

- model training;
- new target-period scoring;
- holdout access or release;
- the K-League `round` experiment;
- external Provider requests;
- API or production secret access;
- formal-weight changes;
- formal-asset changes;
- PR merge or Ready-for-review transition.

`NOT_AUTHORIZED` MUST NOT be described as automatic, imminent, or already in progress.

## 3. Test and business conclusion separation

The following are distinct and MUST NOT substitute for one another:

1. unit tests passed;
2. audit program executed successfully;
3. GitHub Actions workflow completed successfully;
4. Artifact uploaded successfully;
5. production path actually executed;
6. research hypothesis received preregistered experimental support;
7. model is formally usable.

Therefore:

- workflow Success proves only that the recorded workflow completed;
- a skipped job proves that path did not execute;
- a mock network test does not prove real network access;
- Artifact existence does not prove its business conclusions;
- 100% field coverage does not prove predictive value;
- preregistration does not prove an experiment result;
- test PASS does not prove a draw signal is effective.

## 4. Strong-claim hard gates

### 4.1 Existing data exhausted

`EXISTING_DATA_DRAW_SIGNAL_EXHAUSTED_NO_NEW_TRAINING` or an equivalent exhaustion claim is permitted only when ALL are true:

- expected and actual audit asset universes are independent and complete;
- every candidate field has real per-field def-use review;
- PIT and `available_at` are proved;
- every canonical route has a final state;
- `UNRESOLVED=0`;
- missing result evidence is 0;
- no global candidate exists;
- no domain-specific strict-PIT candidate exists;
- no domain-specific reconstructed candidate exists;
- every registered experiment has reviewable results;
- no unverified alias remains;
- no tracked asset is omitted;
- every required production path executed.

Any failed or unknown gate prohibits exhaustion.

### 4.2 Training available

A claim that training can start requires ALL of:

- frozen preregistration;
- proved PIT safety;
- holdout proved untouched;
- frozen inputs, target, splits, and metrics;
- no target leakage;
- separate explicit user authorization.

Without separate authorization, the only permitted state is:

- `PRE_REGISTERED_NOT_RUN`;
- `run_authorized=false`;
- `NOT_AUTHORIZED`.

### 4.3 Field or signal effective

A field or signal may be called effective only after the preregistered experiment actually executes and passes its frozen gates. Coverage, correlation, distribution differences, candidate improvements, and business intuition are insufficient by themselves.

### 4.4 Formally usable

Formal usability requires a completed model, independent holdout, calibration and stability evidence, explicit scope, executed production implementation, fallback behavior, and user authorization to enter formal assets.

### 4.5 Draw problem solved

“Draw problem solved” is prohibited without complete, independent, leakage-free, reproducible evidence covering the actual target scope. A local candidate, one competition, one field, one experiment, or workflow Success cannot support it.

## 5. Scope contract

Domain-specific evidence MUST remain domain-specific. For the current reconstructed `round` candidate, permitted outward claims are limited to:

- `round` satisfies the reconstructed-research preregistration candidate gate in `KOR_KLeague1`;
- predictive gain remains unproved;
- no training occurred;
- no new target-period result was viewed;
- experiment execution is not authorized.

The evidence MUST NOT be expanded to all competitions, a global draw signal, training readiness, formal usability, or a solved draw problem.

## 6. Historical claim withdrawal

When later evidence invalidates or narrows an old claim, the output MUST:

1. identify the old claim exactly;
2. state that it is withdrawn;
3. record the evidence causing withdrawal;
4. state the replacement claim;
5. state the replacement evidence status;
6. preserve the historical record;
7. avoid synonym substitution that preserves the invalid meaning.

The historical claim `EXISTING_DATA_DRAW_SIGNAL_EXHAUSTED_NO_NEW_TRAINING` is withdrawn because the current audit has a `KOR_KLeague1` reconstructed candidate and unresolved routes. The replacement is `PRE_REGISTRATION_REQUIRED_NO_TRAINING_YET`, with no training or new target-period scoring and no run authorization.

## 7. Structured claim record

Every critical claim MUST be represented by a structured record containing at least:

- `claim_id`
- `claim_type`
- `claim_subject`
- `claim_text`
- `evidence_status`
- `scope`
- `exact_head`
- `evidence_refs`
- `execution_status`
- `pit_status`
- `holdout_status`
- `authorization_status`
- `limitations`

Allowed `evidence_status` values are exactly:

- `PROVEN`
- `COMPUTED`
- `INFERRED`
- `UNPROVEN`
- `NOT_AUTHORIZED`

A critical claim missing a status, scope, exact HEAD, or evidence reference MUST fail closed. Natural-language reports MUST be generated from or validated against these records.

## 8. Report output contract

PR receipts, Actions summaries, Artifact summaries, and handoff reports MUST contain:

1. Accurate object: PR, base, branch, previous HEAD, exact new HEAD, Draft status, merge status.
2. Directly proven: `PROVEN` only.
3. Program-computed results: `COMPUTED` only, with input scope.
4. Inferences and candidates: `INFERRED` only.
5. Unproved items: all material `UNPROVEN` claims.
6. Unauthorized actions: all material `NOT_AUTHORIZED` claims.
7. Execution boundary: executed jobs, skipped jobs, mock use, real network requests, Provider access, training, scoring, and formal-asset changes.
8. Cold conclusion: a scope-limited summary that does not expand the evidence.

## 9. Required fail-closed counterexamples

The implementation and tests MUST enforce:

- workflow Success plus skipped live job does not prove the live path;
- 100% `round` coverage plus `PRE_REGISTERED_NOT_RUN` does not prove signal effectiveness;
- `run_authorized=false` and `model_training=0` keeps training `NOT_AUTHORIZED`;
- any unresolved route or missing result evidence prohibits exhaustion;
- an empty field data-flow contract registry prohibits “all fields tested”;
- mock-only networking does not prove real Provider or GitHub network execution;
- Artifact upload alone does not prove business validity;
- unproved PIT prohibits training, holdout release, and formal use;
- an invalidated historical claim requires explicit withdrawal and replacement fields;
- `KOR_KLeague1` evidence cannot produce global or cross-competition claims.

## 10. Current execution and authorization boundary

Until separate explicit user approval:

- `formal_weight=0`
- `model_training=0`
- `new_target_period_scoring=0`
- `provider_network_used=false`
- `external_request_attempts=0`
- `api_football_key_accessed=false`
- `model_diff=0`
- `formal_data_diff=0`
- `config_diff=0`
- `CURRENT_diff=0`
- PR remains Open, Draft, and unmerged
- `round` experiment remains not run
- holdout remains not released

## 11. Cold conclusion template

“V5.0.3 accurate-claim and evidence-boundary contract passed validation on the exact HEAD. This proves only that the audit governance rule and counterexample tests passed; it does not prove `round` has predictive value, does not prove a draw signal is effective, does not authorize training, and does not change `formal_weight=0`. PR #77 remains Open, Draft, and unmerged pending independent Codex review and a later user decision.”
