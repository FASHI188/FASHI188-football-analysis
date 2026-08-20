# FOOTBALL3 INDEPENDENT CURRENT

Updated: 2026-08-20 Asia/Taipei
Project: `football3`
Status: `FULL_STACK_ROOT_CAUSE_REMEDIATION_COMPLETE_NO_NEW_SCIENCE`

## 1. Independent lineage
Football3 scientific root remains immutable:
- experiment: `C072-C`
- root branch: `research/c072c-xg-total-scalar-20260818`
- root SHA: `e3e73c998020beef585cc459a69ea5b73b44ddb3`
- valid continuation: `C072-C -> football3/...`

C073-C077 and descendants remain quarantined. Cross-project information may be used only for explicit comparison or global-consumption exclusion. This remediation does not merge their science into football3.

## 2. Binding scientific state before remediation
Latest executed scientific experiment remains C072-N20, PR #330.

N20 terminal: `C072N20_P1000_PILOT_NO_SIGNAL`.
- exact cohort: 1000; ordered identity SHA `a49e61df94d0f9c368b314829901f0d64d69ad25c51813551a298307e15e56cf`;
- BR318 / GR121 / MLS313 / TR248;
- exact target join 1000/1000;
- dLogLoss `+0.0041598079`;
- bootstrap90 `[-0.0003677775,+0.0086930374]`;
- dBrier `+0.0008985958`;
- dRPS `+0.0001791452`;
- source LogLoss wins 1/4;
- T=2 Top1 fraction 78.0% -> 62.7%, but proper probability quality worsened.

N20 exact hypothesis is PARKed and its 1000 labels are globally consumed. No same-label rescue is allowed.

## 3. Primary scientific target
The primary target remains complete match-level pre-match total-goals probability quality:

`P(T=0,1,2,3,4,5,6,7+)`.

Fixed chain:

`P(T) -> P(home-goal allocation | T,X) -> joint P(H,A) -> derived Draw/1X2/exact-score diagnostics`.

Draw Top1 is downstream. Manual Draw/0-0/1-1/T=2 boosts, post-hoc thresholds/class weights and proper-score sacrifice are forbidden.

## 4. Root-cause remediation completed after N20
PR #331 / branch `football3/full-stack-root-cause-remediation-20260820` converts the previously identified research/process/engineering problems into executable fail-closed controls. No new real target labels, model fits or scientific scores were used for this remediation.

### Scientific-contract fixes
- baseline and candidate must have the same frozen prediction cutoff;
- opening-to-closing information gain may not be described as alpha beyond a same-cutoff closing baseline;
- PIT definition is mandatory and immutable quote timestamps are preferred; coarse opening/closing semantics must be labeled as limited;
- complete P(T) proper-score quality is primary; Top1/Top3 are diagnostics;
- new experiments require a machine-readable frozen contract before target access;
- direct Draw optimization is blocked as the next research route;
- neighboring repairs of viewed PARKed hypotheses are blocked.

### Data/identity/consumption fixes
- zero-label identity/PIT/coverage lock precedes labels;
- exact one-to-one target join is a shared guard;
- cross-project viewed labels remain globally consumed;
- `FOOTBALL_GLOBAL_CONSUMPTION_REGISTRY_V1.json` is the minimum verified registry, while GitHub/Airtable historical search remains mandatory because the registry is not claimed exhaustive for pre-registry history;
- a crash after numeric target materialization consumes those labels even when no scientific metric was emitted;
- an engineering replay after such a crash is reproduction, not fresh confirmation.

### Execution-code fixes
Canonical scientific primitives now live in `football3_core.py`:
- P(T) class order 0,1,2,3,4,5,6,7+;
- half-goal O/U tail mapping 0.5->T>=1, 1.5->T>=2, 2.5->T>=3, 3.5->T>=4, 4.5->T>=5;
- two-way de-vig and nested-tail checks;
- probability conservation;
- LogLoss, multiclass Brier, normalized RPS;
- paired match bootstrap;
- temporal OOS, feature PIT, same-cutoff and exact identity guards;
- sealed-pool guards;
- development-only sample-size planning helper.

New/changed football3 scientific runners must use the shared core and declare a machine-readable experiment contract. Random/shuffled scientific split primitives are rejected. pandas `DataFrame.T` attribute is rejected in changed scientific runners; target access must use explicit `frame['T']`, and matrix transpose must be explicit `.transpose()`.

### Pre-label execution fix
Before real target access, a new experiment must pass:
1. global-consumption/source-revision audit;
2. zero-label identity/PIT/coverage lock;
3. committed exact experiment contract;
4. full synthetic end-to-end scoring smoke using zero real labels;
5. fail-closed contract/runner preflight;
6. explicit user target-access authorization;
7. one-shot target execution.

This prevents a known code-path error from first appearing only after scarce labels have already been consumed.

### Sample-efficiency fix
Arbitrary 100/200/300 confirmation packets are no longer the default. Confirmation requires a frozen power/precision/minimum-N plan derived only from development information or conservative assumptions. Optional stopping is forbidden.

### Method-shopping fix
After labels are viewed, the same labels cannot be rescued by changing hyperparameters, feature subsets, windows, O/U line subsets, smoothing, thresholds/classes, distribution family, source/league subsets, dispersion equation, neighboring transforms, model shell, metrics/gates or stopping rules.

## 5. Historical positive-result re-audit
No historical labels were reopened or re-scored. The executed code/contracts were audited instead.

### C072-F2
Status: `TECHNICALLY_EXECUTED_AS_CONTRACTED_WITH_PIT_LIMITATION`.
- season-forward execution, 8-class probabilities, LogLoss/Brier/RPS and paired bootstrap were technically present;
- no N20-style `.T` scoring bug found;
- source has coarse open/close semantics, not immutable quote timestamps;
- opening reference + close-open movement can reconstruct closing level.

Binding interpretation: F2 supports later/closing market information versus opening market, not incremental same-cutoff closing-market alpha.

### C072-I2
Status: `TECHNICALLY_VALID_EXECUTION_WITH_PREEXECUTION_ENGINEERING_WRAPPER`.
- the raw evaluator contained the same pandas `.T` ambiguity later seen in N20;
- before the one-shot I2 execution, the committed wrapper corrected `scored.T` and `even.T` to explicit `['T']` access;
- the workflow executed that wrapper;
- temporal predict-before-update, proper scores, paired bootstrap, exact-T/division/half consistency and sealed boundaries remain valid.

Binding interpretation: I2 component `D|T` evidence is retained. The formerly local `.T` repair is now a global preflight rule so the defect cannot silently recur in new runners.

### C072-K2
Status: `HISTORICAL_LL_BRIER_PASS_WITH_RPS_AND_SAME_CUTOFF_LIMITATIONS`.
- identity/PIT ordering, joint probability conservation, paired bootstrap and chronological/domain checks were present;
- no N20-style `.T` bug found;
- original joint gate did not compute RPS;
- P(T) path inherited opening-versus-opening+movement information.

Binding interpretation: K2 remains historical LogLoss/Brier evidence, but it is not a full PASS under the corrected V3 mandatory LogLoss+Brier+RPS same-cutoff contract.

## 6. N20 engineering provenance
The raw failed-precursor N20 runner is intentionally retained as immutable historical source to preserve reproducibility of the failure. It contains the failing `test.T` expression. It is not the executable scientific path.

The committed replay wrapper makes exactly the documented one-line correction to `test['T']`, and the N20 workflow executes the replay wrapper. The full-stack execution-surface audit verifies that the defective raw precursor is not directly executed. Future changed scientific runners cannot use `.T` at all.

This is deliberate: preserve historical provenance while fixing the executable path and preventing recurrence.

## 7. Sealed boundaries
Remain sealed/unopened:
- C070-F Confirmation1597;
- N17 reserve266;
- N18C confirmation150;
- any other protected football3 pool not explicitly authorized.

No sealed pool was opened by the full-stack remediation.

## 8. Validation receipt
PR #331 remediation CI:
- `Football3 Full Stack Scientific Preflight` run `32328093077`: SUCCESS;
- 16 unit tests: PASS;
- synthetic end-to-end pre-label smoke: PASS, real target labels opened = 0;
- execution-surface audit: PASS;
- policy/registry/seal validation: PASS;
- changed-scientific-runner migration guard: PASS;
- remediation no-real-target/sealed-data proof: PASS.

Repository-wide `Football Engineering Quality and Security` run `32328093059`: SUCCESS.

Scientific activity during remediation:
- new real target labels opened: 0;
- model fits/scoring on real data: 0/0;
- sealed pools opened: 0;
- C073-C077 scientific evidence imported: 0.

## 9. Current boundary and next step
The engineering/process/root-cause remediation is complete. This does **not** convert N20 or any other failed scientific hypothesis into a PASS and does not imply that the P(T) scientific problem is solved.

Football3 remains scientifically PARKED after N20. The next scientific experiment, only when explicitly authorized, must be a materially new P(T) information/measurement hypothesis with a new preregistered target plan and must pass the V3 pre-label protocol before any target access.

formal_weight remains 0. No downstream Draw optimization is the next step.
