# Football3 R43GOV0 Architecture Audit

Status: research-only governance audit. No CURRENT modification, no main merge, no formal publication, no recomputation of sealed U0/Y0 predictions.

## Audit anchor

- Parent research HEAD: `8e7aa3d703b88fae7e275203afa9d6897db79dcf` (`football3/r43ae0-crosscompetition-entrant-coverage`).
- R43AA0 replay HEAD: `6318753b6ea9f11577096f96a33592521d2c0cea`; successful run `33194733318`.
- R43AA0 imports `bayesian_dynamic_state_oof_v500` as its explicit candidate engine and replays frozen V500-evaluable domains. It does not load the later lineup/player/coach/market/draw modules into its training or prediction matrix.
- R43AA0 is therefore a V500 dynamic-state compatibility replay, not a complete Football3 stack.
- V500 remains the migration reference baseline. Existing collection, lockbox, hashing and settlement chains are preserved.

## Required state vocabulary

Every feature/component must be classified independently at five levels. These levels must never be collapsed into one boolean.

1. `recognized`: parser/resolver can identify the source/entity/field.
2. `pit_legal`: historical source value is demonstrably known by the prediction as-of time.
3. `assembled`: value is present in the numerical feature/component input for that prediction.
4. `numeric_effect`: changing/removing the value can change the numerical prediction matrix/probabilities.
5. `experiment_passed`: the isolated experiment satisfied its preregistered gate.

`recognized != pit_legal != assembled != numeric_effect != experiment_passed`.

## Current activation audit

| Family | Evidence / code lineage | Source and historical coverage | Identity status | PIT / known_at status | Enters R43AA0 numeric path | Numeric effect in current integrated path | Experiment status | Governance conclusion |
|---|---|---|---|---|---|---|---|---|
| V500 baseline matrix + dynamic team attack/defence state | `football-data/validation/bayesian_dynamic_state_oof_v500.py`; R43AA0 `run_r43aa0.py` | 16 frozen-evaluable competitions; R43AA0 aggregate 9203 strict historical OOS rows | team strings resolved by legacy competition-local data path | V500 prediction uses prior matches before target; profile selection uses prior seasons | YES | YES | R43AA0 candidate 48.46% vs 47.66%, +0.804pp; below 53%; only 5 natural draw Top1 / 2 hits | Keep as baseline/reference only; do not describe as complete Football3 |
| Team/player identity bridge | R43A2 availability source bridge | Reep strict person pairs 32,492; strict team pairs 10; internal API->internal player pairs 182,125; team pairs 10,903. Availability player row identity coverage 76.10%; mapped-club starter identity coverage 94.86% | strict one-to-one mappings retained; ambiguous mappings discarded | identity itself is non-label metadata; availability status source is retrospective and NOT PIT-legal as a prematch feature | NO | NO | identity bridge viable; per-fixture availability join not ready | Use as seed evidence for a single canonical resolver; never inject retrospective availability status |
| Probabilistic expected XI / P(start) | R43B0R1 `football3/r43b0r1-probabilistic-lineup-eligible-split`; run `33101081191` | test 6,862 team-sides, 2025-11-20..2026-02-10; prior candidate pool/history only | API player/team identities inside fixture-player source; cold-start players remain unresolved by history | date-safe; target current lineup is evaluation label only; no same-date update before prediction; no current injury status | NO | NO in R43AA0; R43B0R1 explicitly states this stage changes no 1X2 probabilities | gate passed for P(start)/XI-overlap mechanism | Eligible as a PIT lineup-probability input after canonical identity unification; not evidence that Football3 currently uses probable lineup numerically |
| Player technical history | R42H `football3/r42h-player-technical-translation-oos`; run `33091439535` | 570,561 wanted fixture-player rows, 542,820 matched technical rows (~95.14% row match); OOS tech-known-share mean 0.8719; main OOS n=66 | player identities resolved within source spine | historical post-match rows are only used after their match date, but provider collection timestamps are not independently bound per row | NO | NO in R43AA0 | gate FAILED; proper scores worsened; action `DO_NOT_PROMOTE_TECHNICAL_TRANSLATION_V1` | Recognized and historically lagged, but not sufficiently PIT-proven for unified historical feature store and not promotion-eligible |
| Context expected XI -> technical translation | R43F3 | 4,681 1X2 test matches; technical known-share ~99.98% on that slice; matched coach-team rows 39,224 | source-specific identity works on tested slice | target current XI not used as feature; chronology date-safe | NO | YES inside R43F3 experiment only | R43F3 gate passed, but candidate source is R43F0 fatigue + coach rotation + depth and evidence is controlled/postview | Do not migrate the candidate mechanism. At most use it as proof that an expected-XI adapter can feed a numerical outcome layer |
| Fatigue / rotation / ordinary coach / squad-depth interactions | R43F0/R43F3 lineage | source-dependent | source-specific | historical chronology exists in experiment, but user governance marks these mechanisms failed unless genuinely new data exist | NO | NO in R43AA0 | previously failed / not eligible for repetition | Excluded from migration and tuning |
| Retrospective injury/suspension availability | R43A2 pinned `withqwerty/availability-data` | 2,114,169 player-round rows, 2015-2025 | player identity partly bridged; club mapping sparse | NOT PIT-legal: retrospectively scraped; original report-known timestamps not preserved | NO | NO | only valid as retrospective label/evaluation source | Hard-block as historical prematch feature |
| Same-snapshot 1X2 + AH + OU market base | R43Q0 `football3/r43q0-sharp-market-score-base`; run `33176076014`; ledger `football-data/forward/v6_market_first_events_v651.json` | 101 frozen predictions, 83 settled, 53 scored | fixture identity frozen with competition/home/away/kickoff | YES on that prospective ledger: all three surfaces carry the same `surface_observed_at_utc`, before kickoff | NO | YES inside R43Q0 only | architecture gate FAILED; full-volume 53% not met | Component is real numerical prematch code, but historical coverage is tiny and cannot be presented as a 9203-match historical market feature |
| Dynamic bivariate total/difference residual state | R43T0 `football3/r43t0-dynamic-bivariate-residual-state`; run `33176844468` | 83 settled / 53 scored, 2026-07-26..2026-07-31 | inherits market fixture identity | prematch market only; same-kickoff results do not update one another | NO | YES inside R43T0 only | architecture gate FAILED; 0 natural draw Top1 | Extractable as a pure score-matrix component, but frozen parameters only; no retuning on consumed rows |
| Fixed diagonal inflation | R43U0 `football3/r43u0-fixed-diagonal-inflation`; run `33176969016` | 53 scored | inherits R43T0 market identity | inherits valid frozen prematch ledger | NO | YES inside R43U0 only | architecture gate PASSED on consumed 53 rows; 50.94%, +1.887pp vs direct market, proper scores improved, 1 natural draw Top1 / 1 hit; still <53%; action says freeze for new forward confirmation | Extract exact frozen transform as a component; do not tune factor 1.25; do not apply to sealed U0/Y0 predictions |
| R43Y draw calibration / later sealed draw path | existing sealed U0/Y0 governance | sealed forward predictions must remain unchanged | not re-resolved in this audit | no historical or forward labels may be reread to alter sealed predictions | NO | current integration status must not be inferred from naming | implementation location/hash not yet resolved in this targeted audit | Treat as immutable sealed dependency until exact code/hash is located; migration must be adapter-only and bitwise-equivalent before any future use |

## Specific finding: probable lineup numeric effect

Current governance state is `probable_lineup_numeric_effect_enabled = false`.

Evidence supporting the classification:

- R43AA0 contains no lineup/player/coach import into the candidate path.
- R43B0R1 improves expected-XI estimation but explicitly changes no 1X2 probability.
- R43F3 demonstrates a possible numerical bridge from expected XI to 1X2 through R42H, but its candidate expected-XI source contains fatigue/coach/depth interactions that are excluded by current governance, and the underlying R42H v1 gate failed.
- Therefore `recognized lineup data` and `predicted expected XI` must not be reported as `Football3 numerically uses probable lineup`.

The same rule applies to player personnel and coach data. A row-count, match, parse success or identity match is not activation evidence. Activation requires an assembler receipt showing the numerical value entered the prediction input plus a component-level before/after hash or numerical delta.

## Architectural gaps

### 1. No single canonical team identity resolver

Identity logic exists in separate experiments and source bridges. There is no single mandatory resolver shared by training, replay and live inference. This permits the same club to be keyed differently across fixture, market, lineup, coach and player sources.

Required invariant: every source-specific team key resolves to one `canonical_team_id` plus a provenance record. Ambiguous mappings fail closed; fuzzy matching is never silently accepted in formal numerical assembly.

### 2. No single point-in-time feature store

Existing experiments each implement their own chronology rules. There is no shared storage contract requiring `known_at`, `effective_at`, source observation time, lineage hash and leakage class per feature value.

Required invariant: a feature is readable for target `(fixture_id, as_of)` only when `known_at <= as_of`, identity resolution passed, and the source's leakage policy permits numerical use.

### 3. No single feature assembler or activation receipt

Current code can recognize many sources without proving that they affected a prediction. This is the root cause of the lineup/personnel/coach ambiguity.

Every prediction must emit a `feature_activation_receipt` with, at minimum:

- fixture identity and prediction `as_of`;
- canonical home/away team IDs;
- feature family and component version/hash;
- source record count and source hashes;
- identity match count/rate and unresolved/ambiguous count;
- `known_at` pass/fail counts;
- `recognized`, `pit_legal`, `assembled`, `numeric_effect`, `experiment_passed` separately;
- exact numerical feature names used and a deterministic values hash;
- component input matrix hash and output matrix hash;
- skip/fallback reason when not active;
- final unique 1X2 output and final score-matrix hash.

### 4. Training, historical replay and live inference do not share one mandatory call chain

Research scripts currently compose different loaders and transforms. Formal migration requires one orchestration path with mode-specific I/O only; identity resolution, PIT reads, feature assembly and matrix components must be identical code.

### 5. R43Q/R/T/U/Y are experiments, not formal score-matrix components

Q/T/U contain valuable frozen numerical transforms but are embedded in experiment runners. They must be extracted as side-effect-free components with explicit input/output contracts and frozen parameter hashes. Extraction must reproduce existing artifact outputs before any new experiment is permitted.

R43Y must not be reconstructed from memory or naming. Its exact implementation and hash must be located first; sealed U0/Y0 predictions are never rerun during migration.

### 6. Dataset generation and evaluation are fragmented

The generator must emit 100% target-match coverage with one unique 1X2 per match, activation receipts, and PIT evidence. The evaluator must consume those immutable prediction rows and report Top1, LogLoss, Brier, RPS, natural draw Top1 count/hits and chronological blocks.

## Governance decision

R43GOV0 is a rebuild of plumbing and evidence, not a new model search. The first migration target is numerical equivalence with the frozen V500 path while introducing canonical identity, PIT storage, activation receipts and one shared call chain. Only after equivalence is proven may existing frozen Q/T/U/Y components be attached one at a time in shadow/ablation mode. No failed fatigue/ordinary-coach/specialist-state mechanism is reintroduced without genuinely new source data.