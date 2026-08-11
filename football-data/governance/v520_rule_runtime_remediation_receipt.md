# V5.2 Rule / Runtime Remediation Receipt

Status: governance remediation only. No predictive model fit, no label experiment, no provider request, no fixed-sample consumption, no formal-weight change.

## Base

- repository: `FASHI188/FASHI188-football-analysis`
- base: `main@f6532e5a8938e6cd1d0b83639e3acdd3d536edbd`
- branch: `governance/v520-rule-runtime-remediation`

## Confirmed problems addressed

1. Default branch retained a terminal one-time Betfair R2 workflow that checks out an old research HEAD and references `run_pilot_once_r2.py`, while that script is absent from current main. This was a runtime-integrity blocker for whole-repository checks.
2. The associated owner-comment dispatcher was also terminal one-time infrastructure and remained live on main after the one-time authorization had been consumed.
3. The repository did not have one compact machine-readable contract for authentic PIT evidence semantics.
4. The repository did not have one compact unified terminology/gate separating TECHNICAL_PASS, scientific component PASS, confirmation PASS, and formal promotion.
5. Repeated draw/Direct-T research families needed an explicit governance registry to stop new protected samples being spent on minor transformations of already-failed families.

## Changes on this branch

- delete `.github/workflows/football-betfair-draw-trajectory-pilot-run-r2.yml`;
- delete `.github/workflows/football-betfair-draw-trajectory-pilot-run-r2-dispatcher.yml`;
- add `football-data/governance/pit_evidence_contract_v520.json`;
- add `football-data/governance/model_promotion_gate_v520.json`;
- add `football-data/governance/research_family_registry_20260811.json`;
- add this receipt.

## One-time PR cleanup

PR #105 has been closed and its body rewritten to the true terminal status:

`STOP_NO_RESULT_SAMPLE_GATE_FAILED`

Authoritative one-time run `31096153124` stopped before settlement-label access because only 4 synchronized eligible markets were available against the frozen 60–65 requirement. Winner labels read=0, model fits=0, formal_weight=0, rerun_allowed=false.

## V5.2 rule-file relationship

The formal V5.2.0 CURRENT remains a project-file artifact outside this repository. Repository governance files do not replace it and do not grant formal execution authority. V5.2.0 activation requires project File Library uniqueness (`CURRENT_唯一正式规则` count exactly 1) and a fresh startup receipt.

## Predictive boundary

This remediation does **not** claim that Direct-T, exact 7+ tail, D|T, unified score matrix, Draw prediction, lineup effects, market trajectories, or any challenger has become production-ready. It only removes a known stale runtime entry point and codifies the data/validation boundaries required before any future promotion.
