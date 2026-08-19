# C074-K Frozen Contract — Extra-16 Calendar-2025 Exact `T>=7` Forward Confirmation

Status: forward confirmation of the pre-existing V5.1 R1 exact-tail law family. `formal_weight=0`. This contract is frozen after C074-J zero-label PASS and before any 2025 score/result/total/tail label from the 16 frozen `/new/*.csv` files is materialized.

## Scientific question
Can the pre-existing infinite-support V5.1 exact-tail family robustly disaggregate the Direct-T `7+` bucket into `7 / 8 / 9 / 10 / 11+` on a second genuinely untouched external domain, without relaxing the C074-I coverage threshold?

## Frozen source/domain
Football-Data.co.uk `/new/{CODE}.csv` files:
`ARG,AUT,BRA,CHN,DNK,FIN,IRL,JPN,MEX,NOR,POL,ROU,RUS,SWE,SWZ,USA`.

C074-J established 4,713 valid calendar-2025 identities across all 16 files with score/result/tail labels materialized=0, model_fit=0, and zero duplicate identities.

## Frozen chronology
Date-based roles, fixed before labels:
- train: every valid score row with `Date < 2024-01-01`;
- policy: every valid score row with `2024-01-01 <= Date <= 2024-12-31`;
- fit after policy selection: train + policy;
- confirmation test: every valid score row with `2025-01-01 <= Date <= 2025-12-31`.

Rows outside these roles may be downloaded for source audit but cannot influence candidate selection or 2025 confirmation.

Only rows with exact 90-minute total goals `T>=7` enter the tail-law fit/policy/test evaluation. No file/country/match may be removed based on goals, tail frequency, model error, or outcome after labels open.

## Frozen tail representation
`E = T - 7`.
Evaluation bins remain exactly the V5.1 R1 bins:
- `E=0` => T=7
- `E=1` => T=8
- `E=2` => T=9
- `E=3` => T=10
- `E>=4` => T=11+

The selected probability law retains infinite support; `11+` is only an evaluation aggregation.

## Frozen candidate laws
Exactly the old V5.1 R1 catalog; no additions:
1. `pooled_geometric`: `P(E=e)=(1-q)q^e, e>=0`.
2. `pooled_hurdle_geometric`: `P(E=0)=pi`; `P(E=e>=1)=(1-pi)(1-r)r^(e-1)`.

Estimator beta prior alpha = `0.5`, unchanged.

Candidate selection uses pooled calendar-2024 tail LogLoss only. Ties resolve lexicographically by candidate name. Calendar-2025 labels cannot choose family, parameters, files, thresholds, bins, priors, or bootstrap settings.

After selection, the selected family is estimated once on train+policy and applied once to all calendar-2025 tail rows.

## Frozen baseline
Exactly the V5.1 R1 competition-specific five-bin empirical tail baseline:
- bins `7/8/9/10/11+`;
- per-source-code counts from train+policy;
- pooled fit distribution provides the prior shape;
- prior mass = `5.0`.

## Frozen metrics
Primary: exact-tail multiclass LogLoss.
Secondary: multiclass Brier and normalized RPS.
Diagnostics: Top-1, Top-2, exact total counts, selected-law parameters, per-country deltas, probability conservation, pooled bin calibration residual, and tail survival at T>=12/15/20/30/60.

Paired cluster bootstrap exactly follows the old R1 mechanism at the source-country level: 5,000 resamples, seed `51006`, 90% interval `[0.05,0.95]`.

## Frozen data/coverage STOP gate
A scientific verdict is allowed only if all hold:
1. >=12 source countries have valid train score rows;
2. >=12 source countries have valid policy score rows;
3. all 16 source countries have valid calendar-2025 score rows, unless an objective source failure is logged before scoring;
4. pooled train tail rows >=300;
5. pooled policy tail rows >=50;
6. pooled calendar-2025 confirmation tail rows >=80 — **unchanged from C074-I; must not be lowered**;
7. confirmation contains at least three of the first four exact bins (`7,8,9,10`); `11+` may be zero;
8. duplicate `(source_code,Date,Home,Away)` identities across loaded rows = 0.

Failure => `STOP_DATA/COVERAGE`, not scientific failure. No alternate country list or threshold may be searched after labels are opened.

## Frozen CONFIRMATION_PASS gate
All must hold:
1. cluster-bootstrap 90% upper bound for `dLogLoss(model-baseline) < 0`;
2. cluster-bootstrap 90% upper bound for `dBrier < 0`;
3. cluster-bootstrap 90% upper bound for `dRPS < 0`;
4. selected-law probability conservation max absolute residual <= `1e-12`;
5. maximum absolute pooled calibration residual across `7/8/9/10/11+` <= `0.05`, unchanged from V5.1 R1.

Point estimates, Top-k or individual-country wins cannot override the gate.

## Hard stopping rule
After calendar-2025 labels are opened: no new law, no new parameter/grid, no changed bin boundary, no changed alpha, no changed empirical prior mass, no country subset, no tail threshold, no bootstrap seed and no repair on these labels.

PASS => `CONFIRMATION_PASS_EXACT_TAIL` at research-component level, `formal_weight=0`. It may authorize a separately frozen unified research bridge using the independently confirmed Direct-T and D|T components plus a legal tail score mapping, subject to CURRENT V5.2 audit.

FAIL => exact-tail closure remains blocked; no unified matrix/exact-score claim from this chain.

## Protected assets
C071 reserve52180, C070-F Confirmation1597, A05 and all protected samples remain untouched.
