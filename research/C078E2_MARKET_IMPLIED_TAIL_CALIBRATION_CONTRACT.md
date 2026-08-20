# C078-E2 Frozen Contract — Market-Implied One-Parameter Exact-Tail Calibration

Status: research-only / `formal_weight=0`. Frozen before any C078-D numeric score label access.

## Why this version
The first C078-E transport plan was superseded before labels were opened because it would import extra 2024/25 history labels solely to recreate score-history features in new lower-league domains. C078-E2 removes that unnecessary dependency.

C078-E2 uses only the already frozen C078-D market snapshot to define a match-specific full-support Poisson baseline, then fits exactly one global thin-tail calibration parameter on the early block.

## Frozen source
C078-D market-only artifact:
- artifact id `9388656670`;
- digest `sha256:0082eeb77822383138a820f349546212f30038a834f14d1a5aaa89172c739098`;
- market snapshot SHA `98e2865fad8206d41c6087d913e82212b9211da7fdbcc35041bb3810fced7828`;
- identity count `4184`, identity SHA `7762c0f94adf3e734d7fce7f73dd203b61a761fafb733f717b939f3db35423ce`;
- split `2026-01-01`: early calibration `2065`, late confirmation `2119`.

## Match-specific baseline q0
For each frozen market row, compute de-vig closing over-2.5 probability:

`p_over = (1/AvgC>2.5) / [(1/AvgC>2.5)+(1/AvgC<2.5)]`.

Solve the unique `lambda>0` satisfying the Poisson identity:

`P_Pois(lambda)(T>=3) = p_over`.

Numerical inversion is deterministic Brent root finding on `[0.05, 10]` with `xtol=1e-12`, `rtol=1e-12`, maxiter 200.

Baseline conditional exact tail is:

`q0(t|T>=7,X) = Pois(t;lambda) / P_Pois(lambda)(T>=7)`, `t=7,8,...`.

This is a model assumption to be tested, not an identification claim from O/U2.5 alone.

## Frozen candidate calibration
Exactly one global parameter `gamma>=0`:

`q_gamma(t|T>=7,X) ∝ q0(t|T>=7,X) * exp[-gamma*(t-7)]`.

For Poisson this is equivalent to a Poisson with mean `lambda*exp(-gamma)` conditioned on `T>=7`.

No second parameter, movement interaction, league parameter, threshold parameter, mixture, spline, hurdle, COM-Poisson, generalized Poisson, or family search is allowed.

## Score access
Only frozen identities dated `<2026-01-01` may have FTHG/FTAG numerically converted in C078-E2. Late 2,119 score labels must not be converted, stored, hashed, summarized, used for model fit, or used in any metric.

The live 2025/26 CSV bytes may exist transiently only to locate frozen identities. The execution must report `late_numeric_score_access_count=0`.

Early valid score coverage must be >=99.5%; otherwise terminal `STOP_CALIBRATION_SOURCE_COVERAGE`, with late labels still sealed.

## Calibration sample and gamma fit
Calibration uses realized early rows with `T>=7` only.

Coverage gate before gamma interpretation:
- early frozen identities = 2065;
- early valid score coverage >=99.5%;
- realized early T>=7 rows >=35.

Fit gamma by minimizing mean conditional exact-tail LogLoss over those early tail rows only:
- bounds `[0,2]`;
- `scipy.optimize.minimize_scalar(method='bounded')`;
- `xatol=1e-10`, maxiter 2000;
- no restart or alternative objective.

## Calibration metrics
Baseline gamma=0 vs candidate fitted gamma:
- conditional exact-tail LogLoss primary;
- conditional-tail Brier;
- normalized conditional-tail RPS over thresholds 7..59;
- conditional 8+ and 9+ mean calibration residuals;
- probability normalization and residual mass beyond 60.

## Calibration PASS gate
`CALIBRATION_PASS_FREEZE_GAMMA` requires all:
1. market snapshot SHA and identity receipt match C078-D;
2. late numeric score access count=0;
3. early score coverage>=99.5%;
4. early T>=7 count>=35;
5. lambda inversion succeeds for every early tail row;
6. gamma optimizer succeeds;
7. `gamma>1e-6` and `<1.999`;
8. candidate tail LogLoss < baseline;
9. candidate tail Brier <= baseline;
10. candidate tail RPS <= baseline;
11. `abs(r8_candidate) <= abs(r8_baseline)`;
12. `abs(r9_candidate) <= abs(r9_baseline)`;
13. max conditional residual mass beyond T=60 <=1e-8;
14. all conditional distributions normalize within 1e-10.

If any scientific gate fails: `CALIBRATION_FAIL_PARK`; do not open late labels and do not retune gamma/bounds/objective/family on the early sample.

## Confirmation boundary
A C078-E2 PASS only freezes gamma. It does not validate the component. A separate C078-F contract must be frozen before any late score access. C078-F will apply the exact frozen gamma and deterministic lambda inversion to the 2,119 late identities once, with no re-estimation.

## Formal boundaries
CURRENT V5.2.0 unchanged; `formal_weight=0`; C077-B labels quarantined; C071 reserve52,180, C070-F1,597, A05/protected unopened; unified score matrix and exact-score output remain closed.