# Football3 execution standard V2

This document is the single binding execution standard for new football3 science after the 2026-08-20 root-cause remediation hardening. It does not retroactively rewrite historical target outcomes.

## 1. One product task, one master cutoff
The football3 final-prematch product task is frozen at **T-15m**. Baseline and candidate must both use information observable no later than kickoff minus 15 minutes. A different cutoff is a different product contract and may not be silently mixed into the same research program.

Every feature and quote timestamp must be valid and present. Missing, unparsable, stale-by-contract, or post-cutoff timestamps fail closed.

## 2. One experiment, one V2 machine-readable contract
Every new football3 scientific runner must declare:

```python
FOOTBALL3_EXPERIMENT_CONTRACT = "football-data/research/<EXPERIMENT>_EXPERIMENT_CONTRACT.json"
```

The contract must conform to `FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json` and pass `validate_football3_experiment.py` before real target access.

The contract freezes:
- C072-C lineage and `football3/` namespace;
- the T-15m master cutoff;
- exact strong same-cutoff market baseline representation;
- candidate representation and method dimensions;
- immutable source revision and zero-label identity lock;
- external global-consumption audit receipt;
- temporal OOS folds and minimum fold size;
- LogLoss/Brier/RPS plus Top1ECE/ClasswiseECE calibration diagnostics;
- paired bootstrap;
- temporal/domain consistency gates;
- numerical success gates;
- development/confirmation sample plan and stopping rule;
- sealed boundaries.

## 3. Strong same-cutoff market baseline
A baseline description alone is insufficient. The baseline must be a market anchor, use the same T-15m cutoff, use the latest available market snapshot at or before the cutoff, be de-vigged, and have its representation frozen before labels.

Opening-to-closing or earlier-to-later information gains are not same-cutoff alpha against the T-15m baseline.

## 4. Shared scientific primitives only
New football3 scientific runners must import `football3_core` for canonical P(T) classes, O/U tail mapping, de-vig, probability checks, LogLoss/Brier/RPS, Top1ECE/ClasswiseECE, paired bootstrap, exact identity joins, temporal OOS/PIT, master-cutoff validation and sealed-pool guards.

Do not copy/paste neighboring metric, calibration, bootstrap or PIT implementations into a new experiment.

## 5. Mandatory zero-label phase order
Before any new real target value is decoded/materialized:
1. audit source revision and construct zero-label identities;
2. search the minimum registry plus GitHub and Airtable history for cross-project consumption;
3. write an immutable zero-label consumption-audit artifact with receipts;
4. if any overlap or unresolved historical identity gap remains, classify the run only as REPLICATION/REPRODUCTION;
5. freeze the exact V2 experiment contract and all numerical success gates;
6. run synthetic end-to-end scoring with zero real labels;
7. run fail-closed contract/runner/artifact preflight;
8. obtain explicit user authorization for target access;
9. execute once under the frozen contract.

A crash after target materialization consumes those identities globally even when no metric is emitted. Engineering replay is reproduction.

## 6. Confirmation power rule
Arbitrary 100/200/300 confirmation packets are forbidden as a default practice. Confirmation requires, before confirmation labels:
- positive integer minimum N;
- planned power >= 0.80 or an explicitly frozen precision-equivalent plan;
- alpha in (0, 0.20];
- DEVELOPMENT_ONLY or EXTERNAL_PRIOR planning basis;
- immutable planning artifact and SHA256;
- no optional stopping.

## 7. Proper-score and calibration gates
Primary success is LogLoss. The pre-registered LogLoss delta and bootstrap upper-CI gates may not permit worsening. Brier, RPS and ClasswiseECE are mandatory non-inferiority gates. Temporal-fold and domain-consistency thresholds are frozen before labels. Top1/Top3 remain diagnostics only.

## 8. Sealed and consumed boundaries
At minimum these pools remain sealed unless the user explicitly authorizes access:
- C070-F Confirmation1597;
- N17 reserve266;
- N18C confirmation150.

Their tokens/paths are rejected in new scientific runners. The global-consumption registry is a minimum verified register, not an exhaustive pre-registry inventory; therefore fresh evidence additionally requires a zero-label GitHub/Airtable history audit artifact with zero unresolved historical identity gaps.

## 9. Method-shopping prohibition
After labels are viewed, the same labels cannot be rescued by changing hyperparameters, features, windows, O/U lines, smoothing, thresholds/classes, distribution family, league/source subset, dispersion equation, neighboring transform, model shell, metric/gate, calibration scheme or stopping rule.

## 10. Scientific interpretation boundary
These execution controls reduce invalid inference; they do not solve P(T). N20 remains PILOT_NO_SIGNAL/PARK. The next authorized science must be a materially new P(T) information/measurement hypothesis. Direct Draw/0-0/1-1 boosting remains prohibited.
