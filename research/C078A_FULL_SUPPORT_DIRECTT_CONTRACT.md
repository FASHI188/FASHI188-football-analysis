# C078-A Frozen Contract — Full-Support Direct-T NB2 Development

Status: research-only / `formal_weight=0` / development. This contract is frozen before C078-A execution. It does **not** open any new or sealed labels.

## Why C078-A exists

CURRENT V5.2 requires a validated exact tail `q(k | T>=7)` before a unified exact-score matrix can exist. C074-E/G established a stable Direct-T market-movement increment only for the collapsed eight-class target `T=0,1,2,3,4,5,6,7+`. C075/C076 then tested several **post-hoc conditional 7+ tail families** and did not establish a confirmable exact-tail model. That conditional-tail family-shopping path is closed.

C078-A changes the estimand and structure: learn one **unconditional full-support count distribution** `P(T=t | X)` for every integer `t>=0`. Exact-tail probabilities are then a mechanical conditional of that same distribution, not a second model fitted after entering 7+.

## Frozen scientific question

On the already-viewed C074-E development domain, does one global NB2 over-dispersion parameter improve a full-support Direct-T model over the nested Poisson full-support baseline, while also producing a stable and calibrated exact 7+ tail and preserving useful collapsed `0..6,7+` quality?

This is one preregistered family comparison only: **Poisson vs NB2**. No ZIP, hurdle, COM-Poisson, generalized Poisson, mixture, spline-hazard, per-league dispersion, feature-dependent dispersion, threshold-specific tail parameters or family search is authorized in C078-A.

## Frozen data boundary

Reuse only the already-open C074-E development source and labels:
- source family: `nm2890/football-data`;
- pinned source revision: `279978313f9c16a210fa80e8986fa22f0f866fba`;
- same frozen five expanding OOS test seasons: `2019-2020`, `2020-2021`, `2021-2022`, `2022-2023`, `2023-2024`;
- for each fold, training uses eligible seasons strictly earlier than the test season;
- same-date fixtures are featurized before any same-date result update;
- both teams require at least 8 strictly prior results.

C077-B's 6,943 consumed confirmation labels are quarantined and must not be read, joined, scored, summarized for model choice or used to set any C078-A parameter/gate. C074-G 2025/26 confirmation labels are also not C078-A development input.

Protected/sealed assets remain unopened: C076-D 4,567, C071 reserve 52,180, C070-F 1,597, A05 and all protected samples.

## Frozen information set X

Exactly the C074-E candidate information architecture, no feature search:
1. competition total-goals prior mean;
2. competition total-goals prior population sd;
3. home prior GF mean;
4. home prior GF population sd;
5. home prior GA mean;
6. home prior GA population sd;
7. away prior GF mean;
8. away prior GF population sd;
9. away prior GA mean;
10. away prior GA population sd;
11. `log1p` home prior result count;
12. `log1p` away prior result count;
13. frozen `movement_logit = logit(devig_over_close) - logit(devig_over_open)` from the same O/U2.5 opening/closing columns.

Train-fold median imputation and train-fold standardization are applied to these 13 inputs. No other market level, line, bookmaker, interaction, polynomial, window or transform is permitted.

## Exact target

`T_exact = FTHG + FTAG`, with no `min(T,7)` collapse in the primary full-support models.

The model probability space is all nonnegative integers `0,1,2,...`.

## Frozen baseline — Poisson full-support Direct-T

For standardized/imputed `x`:

`log(mu) = beta0 + beta'x`

`T | X ~ Poisson(mu)`.

Fit `beta` by unpenalized maximum likelihood on each training fold only. Numerical optimizer: deterministic L-BFGS-B, zero-vector initialization, maximum 5,000 iterations, tolerance `1e-10`. For numerical safety only, linear predictors are clipped to `[-20,20]` inside likelihood/prediction. No coefficient search or regularization search.

## Frozen candidate — NB2 full-support Direct-T

The mean architecture is identical:

`log(mu) = beta0 + beta'x`.

Candidate distribution is NB2 with one **global**, feature-independent dispersion `alpha>0`:

`Var(T|X)=mu + alpha*mu^2`.

Equivalently `r=1/alpha`, with PMF

`P(T=t)=Gamma(t+r)/(Gamma(r)t!) * (r/(r+mu))^r * (mu/(r+mu))^t`.

Fit `(beta, log(alpha))` jointly by unpenalized maximum likelihood on each training fold only. Deterministic L-BFGS-B; beta initialized from the fitted Poisson fold; `log(alpha)` initialized at `log(0.2)` only as an optimizer start, not a selected scientific parameter; maximum 5,000 iterations; tolerance `1e-10`; numeric bounds `alpha in [1e-6,10]`. No restart/family/dispersion search after labels are scored.

The candidate is nested toward Poisson as `alpha -> 0`. Only this one extra global dispersion degree of freedom distinguishes the scientific candidate from the baseline.

## Frozen C074-E categorical benchmark

For practical compatibility auditing only, reconstruct on exactly the same fold rows the frozen C074-E models:
- categorical score-history baseline: median impute -> StandardScaler -> multinomial LogisticRegression `C=0.1`, no class weights, target `min(T_exact,7)`;
- categorical movement candidate: same + `movement_logit`.

These are not used to fit or recalibrate Poisson/NB2. They are benchmark comparators only.

## Probability and full-support audit

For Poisson/NB2, PMF and survival probabilities must be evaluated analytically from the fitted distribution. Numerical audit uses `t=0..60` plus analytic survival beyond 60 and must satisfy:
- `sum_{0..60} p(t) + P(T>=61) = 1` within `1e-10` per row;
- every PMF/survival value finite and nonnegative;
- no probability renormalization based on the realized target.

Exact-tail is mechanical:

`q(t | T>=7,X) = P(T=t|X) / P(T>=7|X)`, for every integer `t>=7`.

## Frozen OOS metrics

### A. Full exact-T metrics — all eligible OOS rows
Primary: exact-count LogLoss `-log P(T_exact)`.

Secondary proper scores:
- exact-count Brier over `0..60` with analytic residual-tail audit; max residual beyond 60 must be <= `1e-8` for scored rows;
- normalized discrete RPS/CRPS over thresholds `0..60`, divided by 60; residual-tail audit reported.

Report Top-1 exact T only as a diagnostic.

Paired match bootstrap for candidate-minus-baseline exact-T LogLoss: 5,000 resamples, seed `78001`, 90% interval.

### B. Exact 7+ conditional-tail metrics — only realized `T_exact>=7` OOS rows
For each model compute its own mechanical `q(t|T>=7,X)`.

Primary tail metric: conditional exact-tail LogLoss.
Secondary: conditional-tail Brier and normalized RPS over `7..60`.

Paired bootstrap candidate-minus-baseline tail LogLoss: 5,000 resamples, seed `78002`, 90% interval.

Fold tail metrics are interpreted only for folds with at least 30 realized `T>=7` rows. No tail rows may be added/replaced after scoring.

### C. Frozen absolute tail-calibration checks
On realized `T>=7` rows, for thresholds `8+` and `9+`, compute model conditional survival probability and residual:

`r_K = P(T>=K | T>=7,X) - 1[T>=K]`.

For the NB2 candidate, paired/match bootstrap 90% CI of the **mean residual** uses 5,000 resamples with seeds `78008` for 8+ and `78009` for 9+.

Calibration PASS for a threshold requires the 90% CI to contain zero. These are hard C078-A gates because C075-E's earlier simple tail law failed specifically at the first 7->8 step.

### D. Collapsed eight-class compatibility
Collapse each full-support distribution mechanically to `0,1,2,3,4,5,6,7+` and score eight-class LogLoss/Brier/RPS.

Hard compatibility guard: pooled collapsed NB2 LogLoss must be **strictly lower** than the reconstructed frozen C074-E **score-history-only categorical baseline** on the same pooled OOS rows. The C074-E movement categorical candidate is reported as a stronger diagnostic benchmark but is not a C078-A PASS gate.

This guard requires the new full-support architecture to retain useful total-goal discrimination rather than solving the tail by sacrificing the main body of `P(T)`.

## Frozen C078-A scientific PASS gate

Every item below must pass:
1. pooled full exact-T `dLogLoss(NB2-Poisson) < 0`;
2. full exact-T paired-bootstrap 90% upper `dLogLoss < 0`;
3. at least 4/5 chronological folds have full exact-T `dLogLoss < 0`;
4. pooled full exact-T `dBrier <= 0`;
5. pooled full exact-T normalized `dRPS <= 0`;
6. pooled exact-tail conditional `dLogLoss(NB2-Poisson) < 0`;
7. exact-tail paired-bootstrap 90% upper `dLogLoss < 0`;
8. pooled exact-tail `dBrier <= 0`;
9. pooled exact-tail normalized `dRPS <= 0`;
10. strict majority of tail-eligible chronological folds (fold tail n>=30) have tail `dLogLoss < 0`, with at least 3 eligible folds;
11. candidate 8+ conditional-tail mean-residual bootstrap 90% CI contains 0;
12. candidate 9+ conditional-tail mean-residual bootstrap 90% CI contains 0;
13. collapsed NB2 pooled eight-class LogLoss < reconstructed C074-E score-history-only categorical baseline pooled LogLoss;
14. probability/full-support audits pass: max conservation residual <=1e-10, max scored residual mass beyond 60 <=1e-8, no invalid PMF;
15. all five fold optimizations converge for both Poisson and NB2 and no candidate `alpha` is stuck at a numerical bound.

If every item passes: terminal `FULL_SUPPORT_DIRECTT_DEVELOPMENT_PASS`.

Otherwise: terminal `FAIL_PARK`. No rescue on these labels.

## Stopping rule / anti-shopping rule

C078-A authorizes exactly this Poisson-vs-global-NB2 comparison. After OOS metrics are visible, do **not**:
- change the distribution family;
- make dispersion feature-dependent or league-specific;
- change alpha bounds/initialization to rescue a result;
- add zero inflation, hurdle, mixtures, threshold hazards, splines or interactions;
- change feature set, history minimum, fold dates, source subset, market transform or optimizer based on the result;
- alter tail minimum n, bootstrap seeds, calibration thresholds or PASS gates.

A FAIL parks C078-A. A later experiment would require a new named structural hypothesis and new preregistration; it cannot be a silent C078-A repair.

A PASS is still **development-only**. It does not authorize C076-D 4,567 or any other sealed pool. Before any independent confirmation labels are opened, a separate zero-label source/identity gate and frozen confirmation contract are required. C077-B labels may never serve as C078 confirmation because they are already viewed/consumed.

## Formal boundaries

Regardless of C078-A result:
- `formal_weight=0`;
- CURRENT unchanged;
- main unchanged;
- exact-score/unified matrix remains closed until a separately validated exact-tail/full-support component and integration audit satisfy CURRENT V5.2;
- no EV/value claim follows from this research component.
