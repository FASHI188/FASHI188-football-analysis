# C074-I Frozen Contract — Untouched 2025/26 Exact `T>=7` Forward Confirmation

Status: forward confirmation of the pre-existing V5.1 R1 exact-tail law family. `formal_weight=0`. This contract is frozen after C074-H zero-label PASS and before any score/result/total/tail label from the 14 frozen 2025/26 divisions is materialized.

## Scientific question
Can the pre-existing infinite-support V5.1 exact-tail family robustly disaggregate the Direct-T `7+` bucket into `7 / 8 / 9 / 10 / 11+` on a genuinely untouched 2025/26 external domain?

## Frozen confirmation divisions
Exactly the C074-H set:
`E1,E2,E3,SC0,SC1,SC2,SC3,D2,I2,SP2,F2,P1,G1,T1`.

C074-H established 4,726 valid 2025/26 identities across all 14 divisions with result/tail labels materialized=0 and model_fit=0. The seven C074-G top divisions are excluded.

## Frozen source and chronology
Source: public Football-Data.co.uk season CSVs.

For each frozen division, retrieve score-only identity/result columns from 2009/10 onward when available. Missing historical files are logged and never replaced by outcome-dependent selection.

Chronological roles are fixed:
- **train:** all available valid seasons 2009/10 through 2023/24;
- **policy:** 2024/25 only;
- **fit after selection:** train + policy;
- **confirmation test:** 2025/26 only.

Only matches with exact 90-minute total goals `T>=7` enter the tail-law fit/evaluation. No competition, match or total is dropped based on model error or tail size after labels are opened.

## Frozen tail representation
`E = T - 7`.
Evaluation bins are exactly the V5.1 R1 bins:
- E=0 => T=7
- E=1 => T=8
- E=2 => T=9
- E=3 => T=10
- E>=4 => T=11+

The probability law itself must retain infinite discrete support; the `11+` bin is only an evaluation aggregation.

## Frozen candidate laws
Exactly the pre-existing V5.1 R1 catalog; no additions:

1. `pooled_geometric`
   - `P(E=e)=(1-q)q^e, e>=0`
   - beta prior alpha = `0.5` in the frozen estimator.

2. `pooled_hurdle_geometric`
   - `P(E=0)=pi`
   - `P(E=e>=1)=(1-pi)(1-r)r^(e-1)`
   - beta prior alpha = `0.5` in the frozen hurdle estimators.

Candidate selection metric: **policy exact-tail LogLoss only** on the pooled 2024/25 tail rows. Ties resolve lexicographically by candidate name. 2025/26 labels cannot select the law or parameters.

After policy selection, the selected family is re-estimated once on train+policy and applied once to every 2025/26 tail row.

## Frozen baseline
Exactly the V5.1 R1 competition-specific smoothed empirical tail baseline:
- five bins `7/8/9/10/11+`;
- competition counts from train+policy;
- pooled fit distribution as prior shape;
- prior mass = `5.0`.

## Frozen metrics
Primary proper score: exact-tail LogLoss.
Secondary proper scores: multiclass Brier and normalized RPS.
Diagnostics: Top-1, Top-2, exact-tail counts, selected-law parameters, tail survival at T>=12/15/20/30/60, per-division deltas, probability conservation, and max absolute pooled bin calibration residual.

Paired cluster bootstrap: exactly the V5.1 R1 mechanism, resampling competition/division groups with replacement. `5,000` resamples, seed `51006`, interval `[0.05,0.95]`.

## Frozen data/coverage STOP gate
A scientific verdict is allowed only if all hold:
1. >=10 frozen divisions have usable train tail rows;
2. >=10 frozen divisions have usable policy rows or at least one policy tail event across the pooled policy period; division absence is reported, never result-selected;
3. all 14 frozen divisions have valid 2025/26 score files or any unavailable division is an objective source failure recorded before scoring;
4. pooled train tail rows >=300;
5. pooled policy tail rows >=50;
6. pooled 2025/26 confirmation tail rows >=80;
7. confirmation contains at least three of the first four exact bins (`7,8,9,10`); `11+` may be zero because it is inherently sparse.

If this gate fails, terminal is `STOP_DATA/COVERAGE`, not scientific FAIL, and no alternate division list/threshold is searched on the opened labels.

## Frozen CONFIRMATION_PASS gate
All must hold:
1. paired cluster-bootstrap 90% upper bound for `dLogLoss(model-baseline) < 0`;
2. paired cluster-bootstrap 90% upper bound for `dBrier < 0`;
3. paired cluster-bootstrap 90% upper bound for `dRPS < 0`;
4. selected-law probability conservation max absolute residual <= `1e-12`;
5. maximum absolute pooled calibration residual across `7/8/9/10/11+` <= `0.05` (the pre-existing V5.1 R1 threshold).

Point estimates, Top-k, or individual league wins cannot override this gate.

## Hard stopping rule
After 2025/26 labels are opened:
- no new law;
- no parameter grid;
- no changed bin boundary;
- no changed beta prior;
- no changed empirical prior mass;
- no league subset;
- no tail threshold;
- no bootstrap seed;
- no repair on the same labels.

PASS => exact-tail research component gets `CONFIRMATION_PASS`, still `formal_weight=0`. It may authorize a separately frozen unified research bridge with already-confirmed Direct-T and D|T components, subject to CURRENT V5.2 audit and legal score mapping.

FAIL => exact-tail closure remains blocked. No unified matrix or exact-score claim may be produced from this chain.

## Protected assets
C071 reserve52180, C070-F Confirmation1597, A05 and protected samples remain untouched.
