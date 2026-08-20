# Football3 execution standard V1

This document is binding for new football3 scientific execution after the 2026-08-20 root-cause remediation. It does not retroactively rewrite old target outcomes.

## 1. One experiment, one frozen machine-readable contract
Every new football3 scientific runner must declare a module-level constant:

```python
FOOTBALL3_EXPERIMENT_CONTRACT = "football-data/research/<EXPERIMENT>_EXPERIMENT_CONTRACT.json"
```

The contract must pass `validate_football3_experiment.py` before real target access. The contract records scientific root, exact baseline/candidate cutoff, PIT definition, data/identity plan, temporal OOS design, proper-score metrics, paired bootstrap, sample/power plan, sealed boundaries and frozen method dimensions.

## 2. Shared scientific primitives only
New football3 scientific runners must import `football3_core` for:
- canonical total-goal class order `0,1,2,3,4,5,6,7+`;
- O/U half-goal tail mapping and two-way de-vig;
- probability validation and conservation;
- LogLoss, multiclass Brier and normalized RPS;
- paired match bootstrap;
- exact identity joins and disjointness;
- temporal OOS/PIT guards;
- sealed-pool access guards.

Do not copy/paste neighboring metric or bootstrap implementations into a new experiment.

## 3. Mandatory phase order
Before any new real target value is numerically decoded/materialized:
1. audit GitHub/Airtable global consumption and source revision;
2. lock zero-label identities/coverage/PIT;
3. commit exact experiment contract;
4. run full synthetic end-to-end scoring smoke with zero real labels;
5. run fail-closed contract/runner preflight;
6. obtain explicit user authorization for target access;
7. execute once under the frozen contract.

If a process crashes **after** real target values are materialized, those identities are globally consumed even when no metric was produced. An engineering-only replay must be labeled reproduction.

## 4. Known defect classes that now fail closed
- pandas `DataFrame.T` attribute in scientific runners is forbidden. Target access must be explicit `frame["T"]`; matrix transpose must be explicit `.transpose()`.
- random train/test and shuffled CV primitives are forbidden for scientific OOS.
- baseline and candidate prediction cutoffs must match exactly.
- Top1/Top3 cannot substitute for LogLoss/Brier/RPS.
- arbitrary 100/200/300 confirmation packets are not allowed without a frozen power/precision justification.
- same-viewed-label rescue by hyperparameter, feature, line, family, league/window, threshold or metric shopping is forbidden.

## 5. Historical evidence boundary
Historical experiments remain historically recorded, but corrected interpretation is binding:
- C072-F2: technically executed, but primarily opening-to-closing information gain; coarse quote-time semantics; not same-cutoff closing-market alpha proof.
- C072-I2: executed through a committed engineering wrapper that fixed the known pandas `.T` ambiguity before the one-shot run; component `D|T` evidence retained.
- C072-K2: historical LogLoss/Brier joint evidence retained; it did not satisfy the new mandatory RPS + same-cutoff end-to-end standard and may not be described as a V3 full-contract PASS.
- C072-N20: engineering bug fixed and replayed under the original frozen contract; scientific terminal remains NO_SIGNAL/PARK.

## 6. Sealed and global boundaries
At minimum, these football3 pools remain sealed unless the user explicitly authorizes access:
- C070-F Confirmation1597;
- N17 reserve266;
- N18C confirmation150.

The global-consumption registry is a minimum verified register, not a substitute for historical GitHub/Airtable search. Any label viewed in another football project is consumed globally.

## 7. CI policy
The full-stack remediation workflow must pass before this remediation is considered complete. Future football3 scientific runner changes are required to pass static runner guards and the shared-core tests. New scientific runners without a declared contract path are rejected.
