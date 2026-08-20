# Football3 root-cause correction — 2026-08-20

Classification: governance/scientific-priority correction only. No target labels opened, no model fit, no scoring, no formal promotion.

## Why this correction exists

A full historical audit showed that the project did not start from a fundamentally wrong forecasting objective. The recurring failure pattern is better explained by research-priority drift around a hard upstream bottleneck: match-level resolution of the complete total-goals distribution `P(T)`.

This document corrects the football3 operating policy without importing C073-C077 evidence from the separate football project.

## Corrected responsibility diagnosis

### Primary scientific bottleneck

`P(T=0,1,2,...)` does not yet have sufficient stable match-level resolution above the same-cutoff market anchor.

The key distinction is not whether a feature predicts goals marginally; it is whether it contributes conditional information beyond the already-available market state and improves proper scores under chronological OOS.

### Information and representation

The principal research problem is the combination of:

1. weak/limited orthogonal pre-match information after conditioning on a strong market anchor; and
2. insufficient distributional representation when rich inputs are collapsed into a scalar total intensity or a small residual-mean correction.

The next hypothesis should therefore address full distribution shape or its measurement process, not merely another scalar mean adjustment.

### Engine/model family

The optimizer/model shell is secondary. No evidence supports a wholesale engine replacement as the default remedy. A more complex model is not authorized merely because a simpler frozen hypothesis failed.

### Validation rules

Strict chronological OOS, proper scores, bootstrap/fold/domain consistency, preregistration and sealed confirmation are retained. These gates are not treated as failure causes. Relaxing them to create a PASS would invalidate the evidence.

### Research-priority drift

Downstream Draw/Top1 work must not consume fresh evidence while the upstream P(T) gate remains unresolved. The corrected chain is fixed:

`P(T) -> conditional D|T/home-away allocation -> joint score matrix -> derived Draw/1X2/exact score`.

### Sample efficiency

Fresh target labels are a limited scientific resource. Arbitrary small packets are no longer the default. Before confirmation labels are opened, a power/precision and stopping plan must be frozen from development information or conservative assumptions.

### Governance efficiency

Governance must enforce scientific boundaries, not become a parallel research program. New workflow/governance surface area requires a concrete boundary it protects. The football3 continuation state must be project-scoped in Airtable and must never fall through to the shared football-project active state.

## Football3-specific evidence behind the correction

- C072-C: scalar total-intensity point improvement did not pass bootstrap stability.
- C072-E2/F2: earlier movement/open-to-close construction contained P(T) information relative to a weaker reference.
- C072-H2/I2 and K2: downstream conditional score allocation/joint structure can improve proper scores on the football3 line.
- C072-L2: major Top1 concentration localized upstream to P(T).
- C072-N10/N13/N15W: several multi-line/static/dynamic O/U representations failed to establish stable exact-T increment under their frozen contracts.
- C072-N17: 1X2 composition increment worsened proper scores.
- C072-N18C: last-10 shot/xG state as residual mean correction worsened proper scores strongly.
- C072-N19R1: same-line movement adds no stable increment after conditioning on closing O/U2.5; the 1000-label result is replication/reproduction only.

These results do not imply that all multi-line markets, all xG information or all latent models are scientifically closed. They close the exact viewed hypotheses and prohibit neighboring post-view repair on the same labels.

## New admissibility gate for the next experiment

A new football3 experiment may open target labels only after all of the following are frozen:

1. materially new scientific hypothesis;
2. source/revision/match/global-consumption audit;
3. zero-label identity/PIT/coverage audit;
4. same-cutoff baseline definition;
5. representation and model family;
6. exact features and transformations;
7. chronological folds/domain structure;
8. primary and secondary proper scores;
9. bootstrap/consistency gate;
10. sample-size/power/precision plan;
11. confirmation stopping rule;
12. explicit sealed-pool boundary.

Failure at development means PARK for that exact hypothesis. Its reserve cannot be opened as a rescue confirmation.

## Explicitly closed behavior

Until a materially new P(T) candidate clears development:

- no Draw boost/override/threshold research;
- no 0-0/1-1 manual boost;
- no method shopping on N17/N18C/N19R1 labels;
- no same-line movement rescue;
- no neighboring xG mean-correction rescue;
- no opening of C070-F1597, N17 reserve266 or N18C confirmation150;
- no use of C073-C077 results to choose football3's next model/source/stopping rule;
- no formal_weight change.

## Current decision

Football3 remains a viable research program but is PARKED at the current hypothesis boundary. The next action is not another run. It is to define and zero-label audit one materially new P(T) information/measurement hypothesis under the corrected contract.
