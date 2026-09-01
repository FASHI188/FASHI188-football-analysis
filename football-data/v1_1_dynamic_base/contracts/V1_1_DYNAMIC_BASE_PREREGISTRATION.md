# Football3 V1.1 Dynamic Base — Validation Preregistration

Status: FROZEN_BEFORE_IMPLEMENTATION_AND_DEVELOPMENT_RESULTS

## Frozen evidence roles
- Sole model baseline: V1 Artifact `9732754224`, ZIP SHA256 `5f0af0c428f19492715669c8e4fb2451ee94bf373f17f5560ff1a42114375bcb`, head `22f639304d2e32fc952dbec2255153ee45dcd41a`.
- Frozen V1 parameters are the `selected_parameters` already sealed in that Artifact's `evidence/historical_oos_result.json`; they are not re-tuned.
- Development rows only: sealed dataset Artifact `9742771164`, ZIP SHA256 `ddb8999ada449eb2a587c30bca61a88f7e612219abb9903ac07ca1be211b507a`, file `dataset/development.jsonl`, expected n=1826, season 2022/23 only, development SHA256 `b8713ceed6d57ead7b2aadbb24d3154bf5cb5df0d45eef0e762b5c395d6d4fab`. This Artifact is used only as a data carrier; no V2/V2.1 model code or parameter is inherited.
- The already-unsealed 5,256-match block may be accessed only if every development gate passes. If development rejects V1.1, its label vault must not be opened by V1.1.

## Frozen candidate family
All structural choices are fixed by the design contract. Only these 54 combinations exist:
- `dynamic_half_life_days`: [90, 180, 360]
- `dynamic_prior_matches`: [4, 8, 16]
- `dynamic_beta`: [0.05, 0.10, 0.15]
- `dynamic_cross_season_shrink`: [0.40, 0.70]
Fixed constants: `min_effective_evidence=2.0`, `pooled_prior_weight=0.50`, residual denominator `sqrt(mu+0.25)`, residual clip [-2.5,+2.5]. No candidate may be added after development scoring begins.

## Strict-time development protocol
Sort exact kickoff batches by cutoff then competition and fixture ID. Preserve same-kickoff batches atomically.
- Earliest 20% of kickoff batches: warm-up only.
- Next 30%: candidate-selection interval. Each of the 54 fixed candidates is replayed strictly forward; labels update state only after `result_available_at` and only after the corresponding prediction was frozen.
- Final 50%: untouched validation interval split into 8 contiguous time folds with batch boundaries preserved. The selected candidate is frozen before any metric from these 8 folds is read.
Selection rule: lowest selection-interval LogLoss; tie within 1e-12 -> lowest Brier -> lowest RPS -> lexicographically smallest parameter tuple.

V1 is replayed with the exact frozen V1 engine and exact frozen V1 parameters on the same fixtures and release schedule. All reported comparisons are same-match.

## Development metrics and required groups
For V1 and selected V1.1: 1X2 LogLoss, Brier, RPS, Top1, predicted home/away mean goals, actual home/away mean goals. Report every outer fold and every competition×season group in the outer validation union. A sufficiently-sized group is n>=100.

## Development continue gate
Every condition must pass:
1. all deterministic invariants/PIT/identity/batch/future-data guards pass;
2. >=6/8 outer folds have V1.1 LogLoss <= frozen V1 LogLoss (tolerance 1e-12);
3. pooled outer-validation LogLoss gain `V1 - V1.1 >= 0.001`;
4. pooled Brier and RPS do not worsen (tolerance 1e-12);
5. pooled Top1 delta `V1.1 - V1 >= -0.0015`;
6. no competition×season group with n>=100 has V1.1 LogLoss degradation >0.020 versus V1;
7. no competition×season group whose actual home mean exceeds away mean may have predicted V1.1 home mean <= away mean; overall home/away direction must also remain correct;
8. exact-V1 fallback, score-matrix normalization, same-matrix 1X2 and deterministic serialization guards pass.

If ANY development gate fails: write exactly `V1_1_DYNAMIC_BASE_REJECTED`, stop all later scoring, do not access the 5,256 label vault, and do not modify the candidate family or parameters.

Only if ALL development gates pass may the already-unsealed 5,256 block be scored once as `POST_VIEW_DIAGNOSTIC`. That diagnostic is not blind, confirmation, prospective or promotion evidence and may not alter any parameter. Passing development never authorizes a new blind pool, formal weight or model enablement.