# E3e-0 Pure 90-Minute H/D/A Isolated Research Contract

- Contract version: `E3E0-CONTRACT-1.1`
- Rule authority: `足球项目_CURRENT_唯一正式规则_V5.0.2_纯1X2隔离研究轨与联合门控边界维护版.docx`
- Research branch: `research/e3e0-pure-hda-draw-identifiability`
- Base research HEAD: `d0a794be3868ac22d2f5f9cfcacbc46d9942485a`
- Status: `AUTHORIZED_DIAGNOSTIC_RUN`
- formal_weight: `0`
- Automatic promotion: `false`
- Merge authorization: `false`

## 1. Scope

E3e-0 studies only the 90-minute result including stoppage time:

- `P(Home)`
- `P(Draw)`
- `P(Away)`
- 1X2 Top-1

It excludes extra time, penalties and advancement.

E3e-0 does not generate or evaluate:

- exact score;
- total-goal result or distribution;
- BTTS result;
- handicap/OU output or value;
- score-matrix projection.

OU, Asian handicap and other frozen pre-match market observations may be used only as PIT input features. They are not output tasks and are not promotion gates for this isolated track.

## 2. Isolation boundary

E3e-0 must not modify:

- the current formal unified score matrix;
- CURRENT formal model weights;
- formal exact-score, total-goal or BTTS modules;
- formal models;
- processed or raw formal data;
- formal configuration;
- formal registry or activation state.

Exact-score, total-goal and BTTS statuses are `NOT_APPLICABLE` for E3e-0. Their absence, non-execution or non-availability must not cause failure and must not cause fallback to Champion.

Only a later candidate that proposes to modify the shared unified score matrix is subject to four-target joint evaluation. E3e-0 itself is adjudicated only by the preregistered H/D/A metrics below.

## 3. Fixed samples

The diagnostic contract is frozen to:

- full sample: `6,251` matches;
- fixed B100: `100` matches;
- no sample reselection;
- no post-result exclusion;
- no replacement of difficult leagues, seasons or classes.

The combined sample is architecture and identifiability evidence only. Formal promotion requires separate per-competition receipts.

## 4. Strict PIT and rolling OOF

All variants must use strict point-in-time features and rolling chronological OOF:

- no random split;
- no future information;
- no target-season fitting or threshold choice;
- no same-match tuning;
- no post-match threshold adjustment;
- no cross-domain promotion from pooled results.

Hyperparameters, model family, calibration method, thresholds and any class weighting must be chosen only on earlier OOF periods.

## 5. Preregistered feature groups

The first diagnostic stage contains three isolated feature groups:

### A. Market only

Frozen pre-match market features only, including eligible 1X2, OU, Asian handicap and cross-bookmaker disagreement fields.

### B. Team only

PIT team-strength, form, availability and task features with all market-derived features removed.

### C. Market plus team

The union of A and B, with an explicit feature ledger and duplicate-information audit.

No group may silently borrow features from another group.

## 6. Draw identifiability target

The first-layer target is:

`q = P(Draw | X)`

Required reporting:

- actual draw count and draw base rate;
- PR-AUC, with the draw base rate as the reference line;
- ROC-AUC;
- probability calibration and calibration error;
- Precision and Recall in the Top 5%, 10%, 15% and 20% draw-candidate sets;
- highest Recall achievable at Precision not below 30%;
- highest Recall achievable at Precision not below 35%;
- counts of actual draws assigned to Home and Away under final H/D/A Top-1;
- draw identifiability by strength gap, expected-total input, handicap, league and season phase bins.

Top-k sets and fixed-Precision operating points are diagnostic summaries only. They are not executable thresholds and cannot be chosen after seeing target-period outcomes.

## 7. Two-stage H/D/A diagnosis

The second stage is:

- `q = P(Draw | X)`
- `r = P(Home | Non-Draw, X)`

Final probabilities:

- `P(D) = q`
- `P(H) = (1-q) * r`
- `P(A) = (1-q) * (1-r)`

The same OOF and calibration rules apply to both layers.

## 8. Model families

The first diagnostic comparison must include:

- linear Logistic regression;
- a preregistered nonlinear tree model or GAM capable of identifying a narrow draw region.

Class weighting must not be used merely to create more draw Top-1 predictions. If class weighting is evaluated, its probabilities must be recalibrated on an independent earlier OOF period before scoring.

There is no manual draw uplift, no result-aware threshold, no Top-1 override and no post-match class-count correction.

## 9. Required H/D/A metrics

Model selection and diagnosis must report all of the following:

- complete H/D/A Accuracy;
- Macro-F1;
- Balanced Accuracy;
- draw Precision;
- draw Recall;
- draw F1;
- draw PR-AUC;
- LogLoss;
- Brier score;
- RPS;
- H/D/A predicted counts and proportions;
- calibration by probability bin;
- worst rolling window and per-season/per-league stability.

Macro-F1 alone cannot select a model. Accuracy improvements cannot override material degradation in LogLoss, Brier or RPS.

## 10. Required baselines

Every result must be compared item by item with:

- market;
- current formal Champion;
- E3b-1;
- E3d-1.

Selected subsets cannot replace full rolling OOF results. B100 is supplemental and cannot override the full-sample diagnosis.

## 11. Identifiability stop condition

The first-stage question is only:

> Do the existing PIT pre-match features contain useful information for identifying draws?

If draw PR-AUC and preregistered candidate-set Precision do not significantly exceed the observed draw base rate, the experiment must stop. It must not continue by tuning thresholds or increasing draw output volume. The next route must be new PIT feature research.

Significance and uncertainty must be reported with preregistered OOF bootstrap/Wilson intervals. A point estimate alone is not sufficient.

## 12. Promotion boundary

E3e-0 remains `formal_weight=0` regardless of diagnostic outcome.

A later formal candidate requires a separate promotion receipt binding at least:

- `competition_id`;
- target season;
- code SHA;
- feature-contract SHA;
- parameter SHA;
- calibration artifact SHA;
- source-report SHA;
- frozen threshold semantics, if any;
- formal weight and activation meaning.

E3e-0 cannot automatically modify or influence any other formal output.

## 13. Current execution authorization

After V5.0.2 uniqueness activation and explicit user approval, one E3e-0 diagnostic execution is authorized.

The authorized execution may:

- train the preregistered rolling-OOF diagnostic models;
- generate the identifiability and H/D/A comparison report;
- upload a research artifact;
- update the Draft PR with the exact execution receipt.

The authorized execution must not:

- modify formal models, formal data, formal config or CURRENT weights;
- modify the formal unified score matrix;
- merge the Draft PR;
- promote any candidate;
- use post-result threshold tuning, artificial draw uplift or class-weight-only improvement.
