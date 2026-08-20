# C078-E Frozen Contract — Fresh One-Parameter Tail Calibration

Status: research-only / `formal_weight=0` / calibration stage. This contract is frozen before any C078-D score label is numerically accessed.

## Scientific role
C078-A/B showed that the full-support Poisson body and collapsed `0..6,7+` mass are already useful, while the conditional exact tail after entering `7+` is systematically too thick. Same-domain distribution-family shopping is closed.

C078-E therefore does **not** introduce another count family. It freezes one nested calibration transform that preserves `P(T>=7|X)` exactly and changes only the conditional allocation inside `T>=7`.

For baseline full-support Poisson probabilities `p0(t|X)` and tail mass `M(X)=P0(T>=7|X)`, define for one global scalar `gamma>=0`:

`q_gamma(t|T>=7,X) ∝ q_0(t|T>=7,X) * exp[-gamma*(t-7)]`, `t=7,8,...`

and

- `p_gamma(t|X)=p0(t|X)` for `t=0..6`;
- `p_gamma(t|X)=M(X)*q_gamma(t|T>=7,X)` for `t>=7`.

Because the baseline is Poisson, `q_gamma` is exactly a Poisson with mean `mu*exp(-gamma)` conditioned on `T>=7`. `gamma=0` is the frozen baseline. No second tail parameter, threshold-specific parameter, league parameter, feature interaction, spline, mixture or family alternative is authorized.

## Frozen market snapshot
Use only C078-D immutable market-only artifact:
- source run `32317596038`;
- artifact `9388656670`;
- artifact digest `sha256:0082eeb77822383138a820f349546212f30038a834f14d1a5aaa89172c739098`;
- identity count `4184`;
- identity SHA `7762c0f94adf3e734d7fce7f73dd203b61a761fafb733f717b939f3db35423ce`;
- market snapshot SHA `98e2865fad8206d41c6087d913e82212b9211da7fdbcc35041bb3810fced7828`;
- split date `2026-01-01`;
- early/calibration identities `2065`;
- late/confirmation identities `2119`.

The late 2,119 score labels remain sealed throughout C078-E.

## Frozen baseline model
Reconstruct the C078-A Poisson full-support architecture exactly:
- pinned `nm2890/football-data` revision `279978313f9c16a210fa80e8986fa22f0f866fba`;
- same 13 features and same feature definitions as C078-A;
- train-fold median imputation and standardization architecture, but for this final pre-fresh baseline fit the imputer/scaler/Poisson coefficients are fit once on **all eligible pinned development rows dated strictly before 2025-07-01**;
- both teams require at least 8 strictly prior results;
- unpenalized Poisson MLE, deterministic L-BFGS-B, zero-vector initialization, max 5,000 iterations, `ftol=gtol=1e-10`, eta clip `[-20,20]`.

No C078-D labels enter baseline coefficient fitting.

## Fresh history seed
To reproduce the same history architecture in the new lower-league domain, use fixed Football-Data 2024/25 history-only files for the same 12 division codes `E1,E2,E3,SC0,SC1,SC2,SC3,D2,I2,SP2,F2,P1`.

Allowed history-only fields: Date, HomeTeam, AwayTeam, FTHG, FTAG. These 2024/25 results are used only to initialize strictly prior team GF/GA and competition total histories before 2025/26. They are not calibration targets and are not scored.

Within 2025/26, all feature generation is strict PIT: same-date fixtures are predicted before any same-date result update. Early calibration outcomes may update history only after predictions for that date are frozen.

## C078-E score-access boundary
Only frozen identities with date `<2026-01-01` may have `FTHG/FTAG` numerically accessed in C078-E.

The live/remote 2025/26 CSV bytes may be downloaded transiently, but for late identities (`>=2026-01-01`) score fields must not be converted to numbers, stored, hashed, summarized, used to update histories, or used in any metric/model. The calibration artifact must explicitly report `late_numeric_score_access_count=0`.

If the current source cannot supply >=99.5% of the frozen 2,065 early identities with valid score pairs, terminal `STOP_CALIBRATION_SOURCE_COVERAGE`; do not open late labels.

## Fresh feature construction and eligibility
Start 2025/26 histories from the fixed 2024/25 history-only seed. Process frozen C078-D market identities chronologically, grouped by date. Market movement comes only from the frozen C078-D market snapshot, never from a refreshed market value.

A 2025/26 early row is eligible for calibration prediction only if both teams have >=8 strictly prior results in the same division-key history before that match.

Hard calibration coverage before fitting gamma:
- early frozen identity count = 2065;
- valid early score coverage >=99.5%;
- eligible early prediction rows >=1200;
- realized eligible `T>=7` rows >=25.

If any fail: `STOP_CALIBRATION_COVERAGE`; gamma is not interpreted and late labels remain sealed.

## Frozen gamma fit
Fit exactly one scalar `gamma` on **eligible early rows with realized T>=7 only**, minimizing mean conditional exact-tail negative log likelihood.

- parameter bounds: `gamma in [0, 2]`;
- deterministic `scipy.optimize.minimize_scalar(method='bounded')`;
- `xatol=1e-10`, maxiter 2,000;
- no restarts, alternate objective, threshold weighting, shrinkage search or post-result bound change.

The baseline is `gamma=0`.

## Calibration-stage metrics
On eligible early realized `T>=7` rows compare calibrated candidate vs frozen Poisson baseline:
- conditional exact-tail LogLoss primary;
- conditional-tail Brier secondary;
- normalized conditional-tail RPS secondary;
- absolute conditional `8+|7+` and `9+|7+` mean residuals;
- full-support probability conservation and tail residual beyond 60.

Because the transform preserves `P(T>=7)` and all `T<=6` probabilities exactly, collapsed `0..6,7+` probabilities must be bitwise/numerically identical to the baseline within `1e-12`.

## Frozen calibration gate
C078-E reaches `CALIBRATION_PASS_FREEZE_GAMMA` only if all hold:
1. source/identity/market snapshot digests match the frozen C078-D receipt;
2. late numeric score access count = 0;
3. early score coverage >=99.5%;
4. eligible early rows >=1200;
5. eligible early `T>=7` rows >=25;
6. optimizer converges;
7. fitted `gamma > 1e-6` and `<1.999` (not at either bound);
8. early conditional-tail dLogLoss(candidate-baseline) < 0;
9. early conditional-tail dBrier <= 0;
10. early conditional-tail dRPS <= 0;
11. absolute mean residual for conditional 8+ does not worsen: `abs(r8_candidate) <= abs(r8_baseline)`;
12. absolute mean residual for conditional 9+ does not worsen: `abs(r9_candidate) <= abs(r9_baseline)`;
13. max probability conservation residual <=1e-10;
14. max conditional residual mass beyond T=60 <=1e-8;
15. collapsed `0..6,7+` max absolute probability difference <=1e-12.

If PASS, output exact frozen gamma, baseline model coefficients, imputer/scaler parameters, history-source file hashes, early identity receipt and calibration metrics. These become immutable inputs to a separately preregistered one-shot late confirmation stage.

If any gate fails: `CALIBRATION_FAIL_PARK` or the relevant STOP state. Do not open late labels and do not retune gamma/family/bounds/objective/features on the early calibration labels.

## Confirmation not authorized by this contract
C078-E **does not** open or score the late 2,119 block. A new C078-F contract must be frozen after C078-E PASS and before any late numeric score access. C078-F must use the exact frozen gamma and baseline preprocessing/model state from C078-E.

## Formal boundaries
Regardless of outcome:
- `formal_weight=0`;
- CURRENT V5.2.0 unchanged;
- main unchanged;
- C077-B 6,943 confirmation labels remain quarantined;
- C071 reserve52,180, C070-F1,597, A05/protected remain unopened;
- unified score matrix and exact-score output remain closed until a separately confirmed full-support/exact-tail component and integration audit pass CURRENT.