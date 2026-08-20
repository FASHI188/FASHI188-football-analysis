# C078-E4 Frozen Contract — Expanded One-Parameter Market-Implied Exact-Tail Calibration

Status: research-only / `formal_weight=0`. Frozen before numeric score access to the C078-E3 supplemental pool. The C078-D late 2,119 block remains sealed.

## Background
C078-E2 was stopped before gamma fitting because the original early calibration block produced only 26 realized `T>=7` rows, below its frozen minimum of 35. No candidate-vs-baseline scientific metrics were computed. C078-E3 subsequently froze 1,082 additional calibration-only identities across `EC,T1,G1` without numeric score access.

C078-E4 is the only authorized expanded calibration. It uses the same scientific model as C078-E2; it does not change gamma, objective, bounds, market-to-lambda inversion or tail family.

## Frozen market pools
### Original early calibration block
From C078-D artifact `9388656670`:
- market snapshot SHA `98e2865fad8206d41c6087d913e82212b9211da7fdbcc35041bb3810fced7828`;
- total 4,184 identities, but only the `<2026-01-01` early 2,065 identities are calibration-authorized;
- C078-E2 previously observed 2,064 valid early score pairs and 26 realized `T>=7` rows;
- the `>=2026-01-01` 2,119 identities remain forbidden for numeric score access.

### New supplemental calibration-only block
From C078-E3 artifact `9388979273`:
- 1,082 identities;
- identity SHA `d6caa480c1cbd6becc29991d0e31da169ba4659e9d3d4ac0562afa625eaa3c46`;
- market snapshot SHA `bdb07dadb84e6d48b8d13d26977dbe59ba1ed479bed9227e880b28a319d4cf02`;
- codes `EC,T1,G1`;
- no numeric scores have been opened before this contract.

## Baseline and candidate — unchanged from C078-E2
For each calibration identity, derive de-vig closing over-2.5 probability
`p_over=(1/AvgC>2.5)/[(1/AvgC>2.5)+(1/AvgC<2.5)]`.

Solve unique `lambda` on `[0.05,10]` satisfying `P_Pois(lambda)(T>=3)=p_over`, deterministic Brent root `xtol=rtol=1e-12`, maxiter 200.

Baseline conditional exact tail:
`q0(t|T>=7,X)=Pois(t;lambda)/P_Pois(lambda)(T>=7)`.

Candidate has exactly one global parameter `gamma in [0,2]`:
`q_gamma(t|T>=7,X) proportional q0(t|T>=7,X)*exp[-gamma*(t-7)]`,
equivalent to Poisson mean `lambda*exp(-gamma)` conditioned on `T>=7`.

Fit gamma on the pooled realized `T>=7` calibration rows only by mean conditional exact-tail LogLoss using `minimize_scalar(method='bounded')`, `xatol=1e-10`, maxiter 2000. No restart, alternative objective, second parameter, league parameter, interaction or family search.

## Label access boundary
- Original early 2,065 identities may be numerically re-read because they are already calibration-consumed.
- Supplemental 1,082 identities may be numerically opened once under this contract.
- Original late 2,119 identities may be located by identity but their FTHG/FTAG values must never be numerically accessed, stored, hashed, totaled or scored. Execution must report `late_numeric_score_access_count=0`.
- Market values always come from frozen market snapshots, never refreshed source odds.

## Coverage gates before gamma interpretation
All must hold:
1. original early valid score coverage >=99.5%;
2. original early realized `T>=7` count exactly 26 (consistency with C078-E2 receipt);
3. supplemental valid score coverage >=99.5%;
4. supplemental realized `T>=7` count >=15;
5. pooled realized `T>=7` count >=45;
6. all market lambdas finite and within root bracket;
7. late numeric score access count=0.

If any fail: `STOP_EXPANDED_CALIBRATION_COVERAGE`; gamma is not interpreted and late labels remain sealed.

## Frozen calibration metrics
Report pooled and block-specific baseline/candidate:
- conditional exact-tail LogLoss;
- conditional-tail Brier;
- normalized RPS over thresholds 7..59;
- conditional 8+ and 9+ mean residuals.
Also audit conditional probability normalization and residual mass beyond T=60.

## PASS gate
`EXPANDED_CALIBRATION_PASS_FREEZE_GAMMA` requires all coverage gates plus:
1. optimizer succeeds;
2. `gamma>1e-6` and `<1.999`;
3. pooled candidate tail LogLoss < baseline;
4. pooled Brier <= baseline;
5. pooled RPS <= baseline;
6. original-early block tail LogLoss candidate <= baseline;
7. supplemental block tail LogLoss candidate <= baseline;
8. pooled `abs(r8_candidate)<=abs(r8_baseline)`;
9. pooled `abs(r9_candidate)<=abs(r9_baseline)`;
10. max conditional mass beyond T=60 <=1e-8;
11. max normalization residual <=1e-10.

If any scientific gate fails: `EXPANDED_CALIBRATION_FAIL_PARK`. Do not retune gamma/bounds/objective, add features or try another tail family on these calibration labels.

If PASS, persist exact gamma, pooled/block calibration metrics, and a calibration-tail receipt. A separate C078-F one-shot confirmation contract must then be frozen before any of the late 2,119 scores are opened.

CURRENT V5.2.0/main/formal weights/unified matrix remain unchanged.