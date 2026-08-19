# C078-B Frozen Contract — Full-Support Thin-Tail COM-Poisson Development

Status: **post-C078-A development only** / research-only / `formal_weight=0`.

This is a new named hypothesis created after C078-A was fully scored and parked. It is not a rescue or reinterpretation of C078-A, and it cannot claim independent confirmation on the already-viewed development domain.

## Evidence motivating this single hypothesis

C078-A established three facts on the frozen five-fold C074-E development domain:
1. full-support Poisson collapsed `0..6,7+` LogLoss (`1.85565464`) was slightly better than the frozen C074-E movement categorical model (`1.85606110`), so the full-support mean/body architecture and entry into `7+` remain useful;
2. NB2 added no stable information: its single global over-dispersion `alpha` hit the frozen lower bound `1e-6` in all five chronological folds;
3. Poisson/NB2 materially overpredicted continuation after entering `T>=7`: pooled candidate mean residual was about `+0.09355` for `P(8+|7+)` and `+0.05052` for `P(9+|7+)`, with both frozen 90% bootstrap intervals entirely above zero.

Therefore C078-B tests exactly one orthogonal structural direction: **sub-Poisson / under-dispersion with a thinner full-support upper tail**.

## Frozen scientific question

Does a single global Conway–Maxwell–Poisson dispersion parameter `nu>=1`, fitted jointly with the same full-support log-intensity model, improve Poisson exact-count proper scores and repair the 7+ continuation calibration without sacrificing the already-good collapsed `0..6,7+` body?

No other thin-tail family is authorized in C078-B.

## Frozen data / information / folds

Exactly reuse C078-A's already-viewed development population and nothing else:
- source: `nm2890/football-data`;
- pinned revision: `279978313f9c16a210fa80e8986fa22f0f866fba`;
- same valid-source and eligibility logic;
- same 13 frozen features: 12 score-history features plus `movement_logit`;
- minimum 8 strictly prior results for both teams;
- same-date predict-before-update;
- test seasons exactly `2019-2020`, `2020-2021`, `2021-2022`, `2022-2023`, `2023-2024`;
- each fold trains only on eligible seasons strictly earlier than the test season;
- train-fold median imputation and standardization only.

C077-B's 6,943 consumed labels are forbidden. C074-G 2025/26 labels are forbidden as C078-B development input. C076-D 4,567, C071 reserve 52,180, C070-F 1,597, A05 and protected samples remain sealed.

## Frozen baseline

Same full-support Poisson as C078-A:

`log(lambda)=beta0+beta'X`

`T|X ~ Poisson(lambda)`.

Fit beta by unpenalized MLE, deterministic L-BFGS-B, zero initialization, max 5,000 iterations, tolerances `ftol=gtol=1e-10`, same numeric linear-predictor clipping `[-20,20]`.

## Frozen candidate — standard COM-Poisson

For every nonnegative integer `t`:

`P(T=t | lambda,nu) = lambda^t / (t!)^nu / Z(lambda,nu)`

where

`Z(lambda,nu)=sum_{j=0}^infinity lambda^j/(j!)^nu`.

Intensity architecture is identical:

`log(lambda)=beta0+beta'X`.

One and only one extra global parameter is allowed:
- `nu = exp(z)`;
- frozen bound `z in [0, log(5)]`, i.e. `nu in [1,5]`;
- `nu=1` is exactly the Poisson nested boundary;
- `nu>1` is the preregistered under-dispersion/thin-tail direction;
- z starts at `log(1.15)` only as an optimizer starting value;
- beta starts from the fold's fitted Poisson beta;
- joint unpenalized MLE via deterministic L-BFGS-B, max 5,000 iterations, `ftol=gtol=1e-10`.

No feature-dependent nu, no league/year nu, no interactions, no mixture, no hurdle, no threshold-specific hazard, no tail-only refit and no calibration/blend.

## Frozen full-support normalization algorithm

For candidate likelihood/prediction use the explicit grid `j=0..100` and log-sum-exp normalization. For each row define unnormalized recurrence ratio after j:

`a_{j+1}/a_j = lambda/(j+1)^nu`.

At `J=100`, if `q=lambda/(101^nu) < 1`, the unnormalized mass beyond 100 is bounded by `a_100*q/(1-q)` because all subsequent ratios are no larger for `nu>=1`.

Every likelihood and prediction evaluation must audit the resulting **relative normalization-tail upper bound**. Scientific execution is invalid unless:
- maximum relative omitted normalization mass <= `1e-12` on every train/test evaluation at the final fitted parameters;
- all probabilities finite/nonnegative;
- probability conservation `sum_{0..100}p_j + bounded residual` is consistent with 1 to numerical tolerance.

No target-dependent truncation or renormalization is allowed.

For scoring, use explicit cells `0..60`; `P(T>=61)` is the fitted distribution survival from the normalized `0..100` grid plus the bounded residual beyond 100. Hard scored residual requirement remains `max P(T>=61)<=1e-8`, matching C078-A.

## Frozen derivatives / unit checks

Candidate implementation must use the exact finite-grid derivatives of the normalized model:
- `d log Z / d log(lambda) = E[T]`;
- `d log Z / d nu = -E[log(T!)]`;
- gradient with respect to z includes chain factor `nu`.

Before scientific scoring, finite-difference tests must verify beta and z gradients on synthetic data with max absolute error <= `1e-5`, and synthetic checks must verify:
- `nu=1` reproduces Poisson PMF/log-likelihood within `1e-10` on the frozen numerical grid;
- larger nu reduces variance/tail survival in a fixed-lambda synthetic case;
- probability conservation;
- same-date PIT history construction.

## Frozen metrics

Exactly reuse C078-A metric definitions and indices:

### Full exact T, all pooled OOS rows
- exact-count LogLoss primary;
- Brier on cells `0..60`;
- normalized RPS over thresholds `0..59`;
- exact-T Top1 diagnostic;
- paired bootstrap candidate-minus-Poisson LogLoss: 5,000 match resamples, seed `78101`, 90% interval.

### Conditional exact tail, realized T>=7
Mechanical conditional probabilities from the same full-support distribution only:

`q(t|T>=7,X)=P(T=t|X)/P(T>=7|X)`.

- conditional exact-tail LogLoss primary;
- Brier over `7..60`;
- normalized RPS thresholds `7..59`;
- paired bootstrap candidate-minus-Poisson tail LogLoss: 5,000, seed `78102`, 90% interval;
- a fold is tail-vote eligible only with >=30 realized tail rows.

### Absolute tail calibration
On realized `T>=7` rows:
- `P(8+|7+) - 1[T>=8]`, bootstrap 5,000 seed `78108`;
- `P(9+|7+) - 1[T>=9]`, bootstrap 5,000 seed `78109`.

A threshold calibrates only if its 90% CI contains zero.

### Collapsed compatibility
Mechanically collapse COM-Poisson to `0..6,7+` and compare on the same pooled OOS identities.
Report frozen C074-E categorical score-history and movement benchmarks plus C078-A Poisson.

## Frozen PASS gate

Every item must pass:
1. pooled exact-T `dLL(COM-Poisson - Poisson) < 0`;
2. exact-T bootstrap90 upper <0;
3. >=4/5 exact chronological folds have dLL<0;
4. pooled exact Brier delta <=0;
5. pooled exact RPS delta <=0;
6. pooled conditional exact-tail dLL<0;
7. tail bootstrap90 upper <0;
8. pooled tail Brier delta <=0;
9. pooled tail RPS delta <=0;
10. at least 3 tail-eligible folds and strict majority of them have tail dLL<0;
11. 8+ conditional-tail calibration 90% CI contains zero;
12. 9+ conditional-tail calibration 90% CI contains zero;
13. collapsed COM-Poisson pooled LogLoss <= collapsed Poisson pooled LogLoss;
14. collapsed COM-Poisson pooled LogLoss < frozen C074-E movement categorical pooled LogLoss;
15. all five Poisson and COM-Poisson optimizers converge;
16. fitted nu is strictly above the nested lower boundary (`nu>1.0001`) in at least 4/5 folds and below `4.999` in all folds;
17. normalization-tail bound, probability conservation and `P(T>=61)` residual audits all pass.

If all pass: `FULL_SUPPORT_THIN_TAIL_DEVELOPMENT_PASS_POSTVIEW`.
Otherwise: `FAIL_PARK`.

## Anti-shopping / stopping rule

After C078-B metrics are visible, do not change:
- COM-Poisson parameterization;
- nu bounds/start;
- normalization grid or tail bound to rescue the outcome;
- feature set, folds, history threshold, source subset or market transform;
- bootstrap seeds/CI;
- 8+/9+ thresholds;
- PASS gates.

Most importantly: **C078-B is the only post-C078-A thin-tail family allowed on this already-viewed development domain.** If it FAILs, do not sequentially try double-Poisson, generalized-Poisson, finite-binomial, exponential tail tilts, mixtures or other neighboring thin-tail families on these same labels. That would be family shopping.

A PASS is still post-view development only. Independent confirmation requires a genuinely fresh zero-label source/identity gate and a separate frozen confirmation contract before any new numeric labels are opened.

## Formal boundaries

Regardless of result:
- `formal_weight=0`;
- CURRENT/main unchanged;
- no C077-B or sealed labels opened;
- exact-tail is not formally promoted by a development result;
- unified score matrix/exact score remains closed until independent validation and integration audits satisfy CURRENT V5.2;
- no EV/value claim.
