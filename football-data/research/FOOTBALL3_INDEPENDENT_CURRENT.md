# FOOTBALL3 INDEPENDENT CURRENT

Updated: 2026-08-20 Asia/Taipei
Project: `football3`
Status: `FULL_STACK_ROOT_CAUSE_REMEDIATION_V2_COMPLETE_NO_NEW_SCIENCE`

## 1. Independent lineage
Football3 scientific root remains immutable:
- experiment: `C072-C`
- root branch: `research/c072c-xg-total-scalar-20260818`
- root SHA: `e3e73c998020beef585cc459a69ea5b73b44ddb3`
- valid continuation: `C072-C -> football3/...`

C073-C077 and descendants remain quarantined. Cross-project information is allowed only for explicit comparison or global-consumption exclusion. No C073-C077 science is merged into football3.

## 2. Scientific state is unchanged by remediation
Latest executed scientific experiment remains C072-N20, PR #330.

N20 terminal: `C072N20_P1000_PILOT_NO_SIGNAL`.
- exact cohort: 1000; ordered identity SHA `a49e61df94d0f9c368b314829901f0d64d69ad25c51813551a298307e15e56cf`;
- dLogLoss `+0.0041598079`;
- bootstrap90 `[-0.0003677775,+0.0086930374]`;
- dBrier `+0.0008985958`;
- dRPS `+0.0001791452`;
- source LogLoss wins 1/4;
- T=2 Top1 fraction 78.0% -> 62.7%, while proper probability quality worsened.

N20 is PARKed. Its 1000 target labels are globally consumed. No same-label rescue is allowed.

## 3. Primary scientific target
Primary target remains complete match-level pre-match total-goals probability quality:

`P(T=0,1,2,3,4,5,6,7+)`.

Fixed chain:

`P(T) -> P(home-goal allocation | T,X) -> joint P(H,A) -> derived Draw/1X2/exact-score diagnostics`.

Draw Top1 is downstream. Manual Draw/0-0/1-1/T=2 boosts, post-hoc thresholds/class weights and sacrificing proper score for Top1 remain forbidden.

## 4. Binding product contract
Football3 now has one master prediction task for comparable final-prematch research:

`T-15m`.

Baseline and candidate must both use information available no later than kickoff minus 15 minutes. Changing the master cutoff creates a different product contract and cannot be silently mixed into this research chain.

A fresh experiment must use a strong same-cutoff market anchor: latest available market snapshot at or before T-15m, de-vigged, with baseline representation frozen before target access.

## 5. V2 execution hardening
PR #331 / branch `football3/full-stack-root-cause-remediation-20260820` now uses:
- `FOOTBALL3_EXECUTION_STANDARD_V2.md` as the single binding execution standard;
- `FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json` for all new science;
- `FOOTBALL3_RESEARCH_POLICY_V3.json` as the only live football3 policy;
- `FOOTBALL3_GLOBAL_CONSUMPTION_AUDIT_TEMPLATE_V1.json` for zero-label cross-project consumption receipts.

Stale authority files were physically removed:
- `FOOTBALL3_RESEARCH_POLICY_V2.json`;
- `validate_football3_research_policy_v2.py`;
- `FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V1.json`;
- `FOOTBALL3_EXECUTION_STANDARD_V1.md`.

The V3 policy validator now fails if any of these stale authority files reappear.

## 6. PIT and execution-core corrections
Canonical primitives remain in `football3_core.py`, now additionally enforcing:
- master cutoff `T-15m`;
- missing or unparsable cutoff timestamps fail closed;
- missing or unparsable feature/quote timestamps fail closed;
- any feature timestamp later than cutoff fails closed;
- LogLoss, multiclass Brier, normalized RPS;
- Top1ECE and ClasswiseECE calibration metrics;
- paired match bootstrap;
- temporal OOS;
- exact one-to-one identity joins;
- P(T) class order and O/U half-goal direction;
- sealed-pool guards.

Changed/new scientific runners still reject pandas `.T`, random/shuffled split primitives and direct downstream Draw/score rescue patterns. Known sealed-pool tokens/paths are also rejected in new runners.

## 7. Global-consumption closure
The minimum global registry remains `FOOTBALL_GLOBAL_CONSUMPTION_REGISTRY_V1.json`, but it is explicitly not treated as exhaustive for pre-registry history.

For fresh evidence, self-report such as `global_consumption_audit=true` is no longer sufficient. Before target access, a new experiment must provide:
1. immutable zero-label identity lock and SHA256;
2. immutable source revision;
3. registry check;
4. GitHub historical search receipt;
5. Airtable historical search receipt;
6. zero-label audit artifact and SHA256;
7. exact consumed-overlap count;
8. exact unresolved-historical-identity-gap count.

If target overlap > 0 **or** unresolved historical identity gaps > 0, the experiment cannot be classified fresh; it is REPLICATION/REPRODUCTION only. Fresh confirmation requires both counts to be zero.

## 8. Validation and sample gates
Before labels, numerical gates are frozen for:
- primary LogLoss delta;
- paired-bootstrap LogLoss upper CI;
- Brier non-inferiority;
- RPS non-inferiority;
- ClasswiseECE non-inferiority;
- temporal-fold win fraction;
- domain win fraction and maximum tolerated domain LogLoss regression.

Top1/Top3 remain diagnostics.

Confirmation additionally requires a positive integer minimum N, planned power >=0.80 (or equivalent frozen precision plan represented by the contract), alpha, planning basis and immutable planning artifact/SHA. Optional stopping remains forbidden. Arbitrary 100/200/300 confirmation packets are not accepted merely because they are convenient.

## 9. Historical evidence interpretation remains unchanged
No historical target labels were reopened or rescored in this remediation.

- C072-F2 remains evidence that later/closing market information improves over opening information, not proof of alpha beyond a same-cutoff T-15m market baseline.
- C072-I2 `D|T` component evidence remains retained; its historical `.T` ambiguity had been corrected by the committed pre-execution wrapper.
- C072-K2 remains historical LogLoss/Brier evidence, but it is not a full current-contract PASS because the old run lacked the present RPS/same-cutoff/calibration requirements.
- C072-N20 remains `PILOT_NO_SIGNAL/PARK`.

## 10. Sealed boundaries
Remain sealed/unopened:
- C070-F Confirmation1597;
- N17 reserve266;
- N18C confirmation150;
- any other protected football3 pool not explicitly authorized.

No sealed pool was opened by either remediation pass.

## 11. Next scientific step
The execution and governance defects are now separated from the scientific P(T) problem. This remediation does **not** claim that the prediction engine is solved.

The next scientific experiment, only under separate explicit authorization, must be a materially new P(T) information/measurement hypothesis, use the frozen T-15m product contract, produce the zero-label global-consumption audit receipt, freeze success/power/domain/calibration gates, pass synthetic/preflight, and only then access target labels once.

A promising architecture remains latent `P(T)` plus a noisy market-measurement layer; that is a scientific hypothesis, not a remediation result and not yet a PASS.

formal_weight remains 0. No downstream Draw optimization is the next step.
