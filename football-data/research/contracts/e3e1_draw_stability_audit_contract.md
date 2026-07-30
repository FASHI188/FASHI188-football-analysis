# E3e-1 Pure 90-Minute H/D/A Draw Stability Audit Contract

- Rule authority: `足球项目_CURRENT_唯一正式规则_V5.0.2_纯1X2隔离研究轨与联合门控边界维护版.docx`
- Base research HEAD: `9587c703f00d56ef0e13cd20f61ecbdf46134449`
- Research branch: `research/e3e1-draw-stability-audit`
- Status: `AUTHORIZED_STABILITY_AUDIT`
- formal_weight: `0`
- Automatic promotion: `false`
- Merge authorization: `false`

## Scope

E3e-1 audits only the stability of the frozen E3e-0 pure 90-minute H/D/A diagnosis. It does not introduce a new model family, feature, class weight, threshold, calibration method or hyperparameter.

The six frozen E3e-0 combinations are reproduced exactly:

- A market / Logistic;
- A market / Tree;
- B team / Logistic;
- B team / Tree;
- C market plus team / Logistic;
- C market plus team / Tree.

Exact score, total goals and BTTS are `NOT_APPLICABLE`. OU and Asian handicap remain frozen PIT input features only.

## Fixed evidence

- Full sample: `6,251` matches;
- Fixed B100: `100` matches;
- No sample reselection;
- Strict chronological rolling OOF;
- No random split;
- No target-season training;
- No class weights;
- No manual draw uplift;
- No post-result threshold;
- No Champion fallback caused by score/total/BTTS non-execution.

## Stability slices

Every frozen model must be audited on:

1. full rolling OOF;
2. modeled-only rows;
3. each Big-Five league;
4. each season-start year;
5. each league-season cell with at least 150 rows;
6. contiguous chronological 500-match blocks;
7. fixed B100.

## Domain pass

A league, season or chronological block passes only when both conditions hold:

1. PR-AUC bootstrap lower 95% bound exceeds that slice's observed draw prevalence;
2. at least one preregistered Top 5/10/15/20 candidate Precision Wilson lower 95% bound exceeds that slice's draw prevalence.

Point estimates alone cannot pass.

## Stability summary

- Cross-league signal: at least 3 of 5 leagues pass;
- Cross-season signal: at least 2 of modeled seasons 2023-2025 pass;
- Robustness established: cross-league signal AND cross-season signal AND fixed B100 confirmation.

These are diagnostic criteria only. No threshold is activated and no model is promoted.

## Stop condition

If robustness is not established, threshold tuning and artificial draw-volume expansion must stop. The next route is new PIT feature research covering team, lineup, task, fatigue and tactical information.

## Protection

E3e-1 must not modify CURRENT, formal models, formal weights, processed/raw data, configuration, registry, formal score matrix, exact-score module, total-goal module or BTTS module.
