# VALIDATION_ABLATION_PREREG

Status: FROZEN_PREREG / RESEARCH_ONLY
Frozen before implementation/evaluation.

## Evaluation order
Strict nested ablation sequence on the same PIT-valid common cohort:
0. V2 team core
1. + player capability
2. + expected lineup
3. + confirmed-lineup route
4. + bench/substitution
5. + coach regime
6. + tactical matchup
7. + fitness/schedule/travel
8. + referee/competition/environment
9. + pre-match process hazard

Each step compares layer N against N-1 on identical match IDs and identical information availability except for the newly enabled layer. Coverage changes are reported separately; no layer may improve apparent metrics merely by dropping hard matches.

## Time protocol
Development/tuning data must precede evaluation chronologically. Hyperparameters, lineup thresholds, Draw-F1 threshold, interaction support thresholds and calibration choices are selected only on development data. Evaluation labels may never alter these settings. Re-used evaluation periods are explicitly POST_VIEW_RESEARCH and never called blind prospective evidence.

## Reported metrics
For every layer and final candidate:
- multiclass LogLoss (primary)
- multiclass Brier
- RPS
- Top1 accuracy
- Draw binary LogLoss, F1 and calibration/ECE
- 0-0, 1-1, 2-2 probability calibration/Brier
- weak-team-win identification under a predeclared pre-match weakness definition
- exact-score / score-matrix LogLoss or proper score
- coverage rate and coverage-grade mix
- league, season, cold-start and data-grade groups
- worst eligible group with n>=100
- uncertainty calibration

## Promotion gate for an added layer
A layer is ACCEPTED into the V1 candidate only if ALL hold on its PIT-valid evaluation cohort:
1. primary multiclass LogLoss improves by at least 0.0010 absolute versus previous layer;
2. paired match bootstrap 95% CI for delta LogLoss has upper bound < 0;
3. neither Brier nor RPS worsens by more than 0.0010 absolute;
4. Draw binary LogLoss does not worsen by more than 0.0020 and Draw calibration ECE does not worsen by more than 0.010;
5. exact-score/score-matrix LogLoss does not worsen by more than 0.0050;
6. no predeclared group with n>=100 worsens LogLoss by more than 0.0100 unless the group-level 95% CI includes zero and global gain exceeds 0.0030;
7. usable coverage does not drop by more than 2 percentage points solely because the layer refuses cases that the previous layer handled;
8. uncertainty calibration does not materially worsen (ECE increase <=0.010 or equivalent preregistered proper calibration score).

Top1 and Draw F1 are secondary diagnostics, never sole promotion criteria.

## Status rules
ACCEPTED: passes all gates and truly reaches score matrices in blind predictor.
REJECTED_ABLATION: implemented with real PIT-valid data but fails one or more gates.
BLOCKED_DATA: insufficient legal/PIT-valid data to evaluate.
CONTRACT_ONLY: interface/class exists but no real permitted data enters predictions.

## Statistical protocol
Paired bootstrap uses match-level resampling, fixed deterministic seed 20260831, 5000 replicates when n>=1000, otherwise 2000. Report point delta and 95% percentile CI. For multiple subgroup diagnostics, no subgroup may be selected after seeing labels as the sole promotion basis.

## Weak-team definition
For each match, the weaker side is the side with lower pre-match V2 team-core win probability before Translator context. The weak-team-win metric is evaluated only from that frozen pre-match definition.

## Failure/invalidation
Any identity mismatch, known_at violation, target-event leakage, scorer/predictor process contamination, unregistered file, source/license conflict, HEAD drift or prediction-SHA mismatch invalidates the affected experiment regardless of metric improvement.