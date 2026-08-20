# C072-N18C — Market-anchored xG/chance-state P(T) DEVELOPMENT contract

## Lineage and evidence class
- project: football3 only
- scientific root: C072-C
- parent: C072-N18B2 HEAD `847e775fec88a9852ff037b865b7cdb95f929ae3`
- N18B2 frozen zero-label cohort: target550 SHA256 `2d995990fcadcbdc14a2f9fadc07c8aba306433f53a174fcf2a55c513005a386`; dev400 IDs SHA256 `55181a078d39d9ac53881aa0c377d6c6cb819c06053bd75609841a13caa1dbdf`; confirmation150 IDs SHA256 `774be269e30254af29614210401b52c23b0f3a4e79a7945e98014d50590ea90f`.
- evidence class: DEVELOPMENT only. This is not confirmation.
- C073-C077 scientific results remain quarantined and may not guide this experiment.
- C070-F Confirmation1597 and all pre-existing sealed reserves remain unopened.

## Outcome-access boundary
Only the frozen 400 DEVELOPMENT Footiqo IDs may have numeric result values requested or materialized.
The 150 `CONFIRMATION_SEALED` IDs are forbidden at the result-request layer: no request may carry a confirmation ID and no returned row with a confirmation ID may be decoded/materialized.
No confirmation label may be used for fitting, stopping, model choice, calibration or adjudication.

## Scientific question
Does strictly historical shot-level chance-generation/xG state add proper-score information about the full prematch total-goal distribution beyond a market-derived O/U2.5 intensity anchor?

## Frozen market anchor
For each match, use only N18B2 zero-label de-vigged closing O/U2.5 `q_over25`.
Define `mu_market` as the unique Poisson mean satisfying `P_Pois(T>=3 | mu_market) = q_over25`, solved numerically with fixed bounds [0.05, 8.0].
No 1X2, BTTS, other O/U lines, opening odds, movement, score history or market interactions are allowed.

## Frozen count family
Both B0 and C use the same full-support Negative-Binomial-2 (Gamma-Poisson) family with mean `mu` and global dispersion `alpha >= 0.0001` in each expanding training fold. Probabilities are evaluated as exact `T=0..6` plus collapsed `T>=7`; full-support tail is computed analytically.

B0 mean:
`log(mu_i) = log(mu_market_i) + beta0`

C mean:
`log(mu_i) = log(mu_market_i) + beta0 + sum_j beta_j z_ij`, j=1..16.

The 16 features are exactly the N18B2 frozen historical chance-state vector, in its existing order. No subset selection, interactions, nonlinear transforms or alternate windows are allowed.

## Frozen estimation
- expanding chronological OOS only; never random split.
- rows are ordered by `(match_time_local, footiqo_id)`.
- fold 1 train first 160, test next 60.
- fold 2 train first 220, test next 60.
- fold 3 train first 280, test next 60.
- fold 4 train first 340, test final 60.
- pooled OOS n = 240.
- feature means/SDs are computed on the current training fold only; SD floor 1e-8.
- optimization: scipy L-BFGS-B on NB2 negative log-likelihood.
- B0: beta0 unpenalized; log(alpha) bounded to alpha in [0.0001, 3.0].
- C: beta0 unpenalized; 16 standardized feature coefficients use fixed L2 penalty lambda=1.0; alpha has the same bounds/rule as B0.
- no hyperparameter search, no alternative optimizer/family/regularization replay based on outcomes.

## Frozen metrics
Primary proper scores on pooled 240 OOS matches:
1. exact/collapsed LogLoss over classes 0,1,2,3,4,5,6,7+;
2. multiclass Brier;
3. RPS over the same ordered classes.

Diagnostics only: Top1 accuracy, Top3 hit rate, fitted alpha, residual-mean magnitude.

Paired bootstrap: 3000 resamples of the 240 OOS rows, seed `72018`, reporting 90% CI for candidate-minus-baseline dLogLoss.

## Frozen PASS gate
N18C DEVELOPMENT PASS requires all:
- pooled dLogLoss < 0;
- paired bootstrap 90% upper bound for dLogLoss < 0;
- pooled dBrier <= 0;
- pooled dRPS <= 0;
- candidate LogLoss better in at least 3 of 4 chronological folds;
- candidate LogLoss better in at least 4 of 6 frozen leagues (EPL, LaLiga, Bundesliga, Serie A, Ligue 1, MLS);
- every predicted vector finite, nonnegative and sums to 1 within 1e-10;
- no outcome-access boundary violation and no technical/source drift.

A separate `BREAKTHROUGH_SCREEN` flag is true only if PASS also has dLogLoss <= -0.010, dRPS <= -0.001, and 4/4 chronological fold LogLoss wins. It does not itself constitute confirmation.

## Stopping rule
If DEVELOPMENT FAIL/PARK, the 150 confirmation labels remain sealed and this exact N18C hypothesis may not be repaired/tuned on the consumed 400 outcomes.
If DEVELOPMENT PASS, no confirmation labels are opened automatically: a separate immutable N18D confirmation contract must first be frozen.
