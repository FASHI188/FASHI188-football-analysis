# V2 Validation Preregistration

Status: FROZEN BEFORE IMPLEMENTATION OR FINAL-LABEL ACCESS

## Chronology
Build one frozen canonical historical universe. Reserve the latest 15% by chronological cutoff as the FINAL HOLDOUT, moving the boundary earlier as needed so a same-cutoff batch is never split. The earlier 85% is research-only. On the research-only period run at least 8 expanding-window outer folds; each outer test block is strictly after its training block and same-cutoff batches are atomic. Hyperparameters/calibration/layer decisions are selected only from inner chronology within the research period.

## Ablation order
A0 independent dynamic team baseline.
A1 joint-score family winner.
A2 player/lineup.
A3 bench/substitution-prior.
A4 coach/tactical regime.
A5 fitness/schedule.
A6 match-process (only if lawful minute data; else BLOCKED_DATA).
A7 independent 1X2 head + minimum-KL reconciliation.
Each added layer must show positive median multiclass LogLoss improvement and nonnegative result on >=6 of 8 evaluable outer folds, while not breaching guards below. Failed/unavailable layers are excluded before final-rule freeze.

## Metrics
Top1; multiclass LogLoss/Brier/RPS; classwise and macro ECE with reliability bins frozen from research period; draw binary LogLoss/Brier, draw Top1 recall/precision/count; full exact-score LogLoss; 0-0/1-1/2-2 binary Brier/log loss/calibration; competition/season; rounds 1/2/3/29/30 where present; promoted/new-team/new-league/cold evidence; lineup state; coach-change slices; worst n>=100 subgroup; coverage/failure.

## Final promotion gates, all required
On exact same-match intersection against V1 frozen research baseline (V1 is read-only reference, not parent):
- multiclass LogLoss improvement >= 0.0030;
- Brier improvement >= 0.0015;
- RPS improvement >= 0.00075;
- Top1 difference >= -0.0015 absolute;
- pure V2 coverage >= 0.990 and not >0.0025 below the eligible comparison universe;
- macro ECE <= 0.030 and no more than 0.005 worse than V1;
- draw binary LogLoss no worse than V1 by >0.002 and draw Brier no worse by >0.001;
- draw Top1 recall >= max(V1 recall + 0.05, 0.08), with draw Top1 precision >= max(V1 precision - 0.03, 0.20) when >=100 actual draws;
- full exact-score LogLoss no worse than the winning pre-final joint family by >0.005;
- 1-1 binary LogLoss/Brier each no worse than the corresponding V1 score-matrix event metric by >0.002, and absolute 1-1 calibration error <= max(V1 error, 0.015);
- 0-0 and 2-2 binary Brier no worse than V1 by >0.002;
- worst n>=100 subgroup multiclass LogLoss delta vs V1 <= +0.025;
- every governance/integrity gate passes and runtime prediction failure rate <=0.005.

Also compare the same final matches against the frozen M10/V500 baseline, but V500 is not the promotion target and cannot enter V2 fitting.

## Final procedure
Before any final result labels are opened, commit selected candidate, all parameters/ranges, retained layers, calibration rule, reliability bins, gates above, final fixture IDs/cutoffs and blind-prediction manifest schema. Run blind final prediction in a label-free process and freeze bytes/hash. Only a separate scorer may read final labels. No post-final retuning.

If any required promotion gate fails: `scientific_status=NOT_PROMOTED`. Engineering success cannot override it.