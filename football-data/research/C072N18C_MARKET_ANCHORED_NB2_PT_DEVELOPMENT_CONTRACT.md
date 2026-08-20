# C072-N18C — Market-Anchored NB2 P(T) development contract

## Status and lineage

- project: `football3`
- scientific root: C072-C `e3e73c998020beef585cc459a69ea5b73b44ddb3`
- parent: C072-N18B2 HEAD `847e775fec88a9852ff037b865b7cdb95f929ae3`
- evidence class: **DEVELOPMENT**
- this contract is frozen before any N18C target-result request.
- C073-C077 and descendants remain scientifically quarantined.
- C070-F Confirmation1597 remains sealed.
- all prior sealed reserves remain sealed.
- N18B2 confirmation150 remains sealed and is forbidden to the N18C result transport.

N18C is the first one-shot scientific test of the N18 route. It asks whether strictly historical shot/xG chance state contains incremental exact-total information after conditioning on a frozen O/U2.5 market anchor.

## Immutable zero-label cohort

N18C must reconstruct the N18B2 zero-label join and reproduce all of the following before any result request:

- selected target rows: 550
- DEVELOPMENT rows: 400
- CONFIRMATION_SEALED rows: 150
- dev400 IDs SHA256: `55181a078d39d9ac53881aa0c377d6c6cb819c06053bd75609841a13caa1dbdf`
- confirmation150 IDs SHA256: `774be269e30254af29614210401b52c23b0f3a4e79a7945e98014d50590ea90f`
- dev400 canonical zero-label semantic SHA256: `dcc32269261fc8c3b1a86e4ea930539d518ee4e9f268c87478d9692f2a414fdd`
- confirmation150 canonical zero-label semantic SHA256: `4c3a8afd917b1c3cd3c6f9de37af331c141d1e2fac314ca2e89ca085d09fae17`
- source-target overlap: 0
- feature count: 16
- target result values materialized at this gate: 0

The canonical semantic hash is SHA256 over UTF-8 newline-delimited JSON with sorted keys and compact separators for the ordered rows. It intentionally avoids gzip-container timestamp nondeterminism.

## Frozen market anchor

Only the N18B2 de-vigged closing O/U2.5 probability `q_over25` is permitted.

For each target match, define `mu_market` as the unique positive solution of

`q_over25 = P_Poisson(T >= 3 | mu_market)`

or equivalently

`q_over25 = 1 - exp(-mu_market) * (1 + mu_market + mu_market^2/2)`.

No 1X2, BTTS, O/U0.5/1.5/3.5/4.5, opening price, movement or other market field may enter N18C.

## Frozen non-market information

Exactly the 16 N18B2 features are used, with no subset selection:

For the home team, then the away team, each calculated from the last 10 strictly prior source matches with a minimum of 8:

1. own xG per match
2. opponent xG per match
3. own shots per match
4. opponent shots per match
5. pooled own xG per shot
6. pooled opponent xG per shot
7. own high-xG (`xG >= 0.20`) chances per match
8. opponent high-xG (`xG >= 0.20`) chances per match

No feature interaction, nonlinear transform, feature dropping, league dummy, player/lineup field or market-derived extra variable is allowed.

## Frozen count-distribution family

Both B0 and C use the identical full-support Gamma-Poisson / NB2 family:

- mean `mu_i > 0`
- dispersion `alpha > 0`
- `Var(T_i) = mu_i + alpha * mu_i^2`

The exact-T evaluation distribution is `P(T=0),...,P(T=6),P(T>=7)` obtained directly from the fitted full-support NB2 mass and the residual tail.

This is a latent-intensity model because NB2 is the Gamma-Poisson mixture. In this first narrow test, dispersion is one global train-fitted scalar in both B0 and C. Chance-state-dependent dispersion is deliberately **not** enabled. That prevents a second axis of model shopping and isolates whether chance state improves the market-anchored mean residual.

### B0 — market-only NB2

`log(mu_i) = log(mu_market_i) + b0`

Train-fitted parameters per fold:
- scalar `b0`
- scalar global `alpha`

### C — market-anchored chance-state NB2

`log(mu_i) = log(mu_market_i) + b0 + beta' z_i`

where `z_i` is the 16-vector standardized using training-fold mean and population SD only.

Train-fitted parameters per fold:
- scalar `b0`
- 16 residual coefficients `beta`
- scalar global `alpha`

The 16 residual coefficients have one frozen Gaussian shrinkage prior / L2 penalty:

- prior SD per standardized coefficient: `0.25`
- objective penalty: `0.5 * sum((beta / 0.25)^2)`

Interpretation: absent reliable incremental signal, C is forced back toward B0. No regularization search is allowed.

## Optimizer and numerical contract

- optimizer: SciPy `L-BFGS-B`
- one deterministic start only; no multistart selection
- B0 initial: `b0=0`, `log(alpha)=-2`
- C initial: B0 fitted `b0` and `log(alpha)`, all beta=0
- bounds: `b0 in [-1,1]`, each `beta in [-1,1]`, `log(alpha) in [-8,2]`
- maximum iterations: 2000
- optimizer failure in either model on any fold is terminal FAIL
- NB2 probabilities must be finite, nonnegative and sum to one within `1e-10` after 7+ collapse

No alternate family, alternate optimizer, alternate penalty, alternate start or post-view numerical rescue is authorized.

## Frozen target-label transport

Only the 400 DEVELOPMENT Footiqo IDs may be requested from result tables.

The 150 confirmation IDs must be loaded into a deny-set before any network request. The runner must assert every requested ID belongs to dev400 and not confirmation150.

Result transport uses one server-side DataTables request per authorized dev ID with an ID-column search. A response is accepted only when:

- the returned row ID equals the requested dev ID;
- the ID is not in the confirmation deny-set;
- source league, season, parsed match time, home team and away team exactly match the frozen zero-label row;
- only after those checks may `FTHG` and `FTAG` be decoded.

Any returned non-requested ID, confirmation ID, ambiguous row count, identity mismatch or schema drift is terminal FAIL before scoring.

The runner must record:
- requested dev IDs count and hash;
- confirmation IDs requested = 0;
- dev result rows materialized;
- dev numeric goal values materialized;
- confirmation numeric goal values materialized = 0.

## Frozen chronological OOS design

The 400 dev rows are ordered by `(match_time_local, footiqo_id)`. Same calendar date is never split between train and test.

Zero-label date boundaries were fixed before target access:

- warm-up training only: through `2024-09-29` inclusive — expected 122 rows
- Fold 1 test: `2024-09-30` through `2024-10-06` — expected 68 rows
- Fold 2 test: `2024-10-07` through `2024-10-25` — expected 63 rows
- Fold 3 test: `2024-10-26` through `2024-11-03` — expected 87 rows
- Fold 4 test: `2024-11-04` through `2024-11-23` — expected 60 rows

For each fold, training is all development rows strictly before that fold's first calendar date. No test outcomes are incorporated until the entire fold has been scored.

Expected pooled strict-OOS rows: **278**.

## Metrics

Primary metric:
- exact-T 8-class LogLoss for `0,1,2,3,4,5,6,7+`

Mandatory secondary proper scores:
- multiclass Brier score
- RPS over ordered `0..6,7+`

Diagnostics only:
- Top1 accuracy
- Top3 accuracy
- mean predicted total truncated/collapsed diagnostic

For every metric, report B0, C and paired difference `C - B0`.

Also report:
- all four chronological fold LogLoss differences;
- all six league LogLoss differences on pooled OOS rows;
- paired bootstrap of pooled dLogLoss: 5,000 resamples, seed `72018`, 90% percentile CI;
- `P(dLogLoss < 0)` from bootstrap replicates.

## Frozen DEVELOPMENT PASS gate

N18C passes only if **all** are true:

1. pooled dLogLoss `< 0`;
2. paired-bootstrap 90% upper bound for dLogLoss `< 0`;
3. pooled dBrier `<= 0`;
4. pooled dRPS `<= 0`;
5. LogLoss improves in at least 3 of 4 chronological folds;
6. LogLoss improves in at least 4 of 6 leagues;
7. all probability and identity/boundary audits pass;
8. both B0 and C optimize successfully in all four folds.

If any condition fails, terminal verdict is `C072N18C_MARKET_ANCHORED_NB2_DEVELOPMENT_PARK`.

If all pass, terminal verdict is `C072N18C_MARKET_ANCHORED_NB2_DEVELOPMENT_PASS` and only then may a separate N18-D contract be written. Passing N18C does **not** itself authorize opening confirmation150.

### Breakthrough screen — diagnostic tier above PASS

A stronger `BREAKTHROUGH_SCREEN_PASS` flag additionally requires:

- pooled dLogLoss `<= -0.010`;
- pooled dRPS `<= -0.001`;
- LogLoss nonworse in all four folds.

This flag cannot rescue a failed DEVELOPMENT PASS and is not required for scientific PASS.

## Anti-shopping / stopping rule

After any N18C dev target value is viewed, the following are forbidden on these 400 labels:

- changing NB2 to Poisson, COM-Poisson, lognormal-Poisson or another family;
- changing the 16-feature set or history window;
- changing feature scaling;
- changing coefficient penalty;
- adding chance-state-dependent dispersion;
- adding league effects;
- adding other market fields;
- changing fold boundaries;
- changing bootstrap settings or PASS gates;
- opening confirmation150 to rescue a PARK;
- retrying a substantively different model on the same viewed labels.

A clean PARK means this exact N18C hypothesis is consumed and parked. Any material new hypothesis requires a new preregistered data plan.