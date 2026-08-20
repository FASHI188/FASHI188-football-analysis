# C072-N18C — market-anchored NB2 P(T) development postrun adjudication

## Verdict

`C072N18C_MARKET_ANCHORED_NB2_DEVELOPMENT_PARK`

This is a clean DEVELOPMENT PARK under the frozen N18C contract. It is not a technical failure and it does not authorize confirmation access.

## Authority

- project: `football3`
- scientific root: C072-C `e3e73c998020beef585cc459a69ea5b73b44ddb3`
- parent: C072-N18B2 HEAD `847e775fec88a9852ff037b865b7cdb95f929ae3`
- N18C branch pre-run HEAD: `c0aba8f6c04828e0c253c8fa81329faf538d0baa`
- PR: #317
- workflow run: `32317334207`
- job: `96272307828`
- PR merge checkout used by Actions: `5b386b8fb174b18dd688157c341bda46024c9d2d`
- artifact: `9388821490`
- artifact zip digest: `sha256:f732decb35fc561515465635770b3c5c0c0e6e2f7dece698a3df9f414d5d0155`
- engineering quality/security guard: PASS
- scientific workflow: PASS as execution, terminal scientific verdict PARK

## Boundary audit

Before result access, the N18B2 zero-label cohort was rebuilt successfully:

- eligible zero-label rows: 611
- selected: 550
- DEVELOPMENT: 400
- CONFIRMATION_SEALED: 150
- dev400 IDs SHA256: `55181a078d39d9ac53881aa0c377d6c6cb819c06053bd75609841a13caa1dbdf`
- confirmation150 IDs SHA256: `774be269e30254af29614210401b52c23b0f3a4e79a7945e98014d50590ea90f`
- source-target overlap: 0
- target result values at zero-label gate: 0

N18C result transport then materialized only the 400 authorized DEVELOPMENT targets:

- development rows opened: 400
- authorized development result rows materialized: 400
- authorized development numeric goal values materialized: 800
- confirmation IDs requested: **0**
- confirmation numeric goal values materialized: **0**
- C070-F Confirmation1597 opened: false
- other sealed reserves opened: false
- C073-C077 scientific results used: false

Therefore the confirmation150 pool remains sealed and scientifically untouched by N18C.

## Frozen model comparison

Both models used the same full-support NB2 / Gamma-Poisson family.

B0:

`log(mu) = log(mu_market) + b0`

C:

`log(mu) = log(mu_market) + b0 + beta' z`

where:
- `mu_market` is implied solely by de-vigged closing O/U2.5;
- `z` is exactly the 16 N18B2 historical shot/xG chance-state features;
- beta prior SD was fixed at 0.25;
- global NB2 dispersion was fitted in both models;
- no feature, family, regularization, market or fold search occurred.

The fitted NB2 alpha was extremely small in every fold (approximately 0.00034–0.00059), meaning the frozen family naturally collapsed very close to Poisson. This is a diagnostic observation only; it does not authorize switching families on these viewed labels.

## Strict chronological OOS

- warm-up train-only rows: 122
- pooled strict-OOS rows: 278
- Fold 1: 68
- Fold 2: 63
- Fold 3: 87
- Fold 4: 60
- same calendar date was never split between train and test.

## Pooled proper scores

### LogLoss
- B0: **1.9020784246**
- C: **1.9350838303**
- dLogLoss `C-B0`: **+0.0330054057**

### Brier
- dBrier `C-B0`: **+0.0070466607**

### RPS
- dRPS `C-B0`: **+0.0050530168**

All three mandatory proper-score directions are adverse.

## Paired bootstrap

5,000 paired resamples, seed 72018:

- bootstrap 90% dLogLoss CI: **[+0.0169596204, +0.0497073826]**
- bootstrap `P(dLogLoss < 0)`: **0.0002**

The full 90% interval is above zero. This is strong counterevidence against an incremental gain for the frozen N18C representation on this development pool.

## Time-fold consistency

Candidate LogLoss minus baseline:

- F1: **+0.0508175032**
- F2: **+0.0264118606**
- F3: **+0.0130116285**
- F4: **+0.0487325611**

LogLoss wins: **0/4**.

The candidate worsened every chronological fold.

## Cross-league consistency

Candidate LogLoss minus baseline:

- Bundesliga: **+0.0207413497**
- EPL: **-0.0024318991**
- LaLiga: **+0.0410695488**
- Ligue 1: **+0.0510812955**
- MLS: **+0.0401197674**
- Serie A: **+0.0420922952**

LogLoss wins: **1/6**. Only EPL was marginally favorable; the other five leagues were adverse.

## Diagnostics

- B0 Top1: 0.2553956835
- C Top1: 0.2553956835
- B0 Top3: 0.6258992806
- C Top3: 0.5791366906
- breakthrough screen: FAIL

Top1 equality cannot override the proper-score deterioration.

## Frozen PASS-gate adjudication

Failed gates:
- pooled dLogLoss < 0: FAIL
- bootstrap90 upper < 0: FAIL
- dBrier <= 0: FAIL
- dRPS <= 0: FAIL
- >=3/4 time-fold LL wins: FAIL (0/4)
- >=4/6 league LL wins: FAIL (1/6)

Passed technical gates:
- optimizer success all folds: PASS
- probability conservation: PASS
- identity/boundary audit: PASS
- confirmation seal: PASS

Final verdict is therefore unambiguously PARK.

## Scientific interpretation

N18C tested a narrow, preregistered question: can the frozen 16-feature historical FotMob shot/xG chance state improve complete P(T) when used as a regularized residual mean correction on top of an O/U2.5 market anchor under the same NB2 family?

On this development pool, the answer is **no**. The candidate is not merely statistically inconclusive; it is materially worse in pooled LogLoss/Brier/RPS, worse in all four chronological folds and worse in five of six leagues.

This does **not** prove that all historical xG/chance information is useless. It does establish strong counterevidence against this exact last-10 equal-weight aggregate feature state + regularized residual-mean NB2 representation.

## Stopping rule

The 400 DEVELOPMENT target labels are now globally consumed for this scientific question.

Forbidden on these labels:
- tuning beta prior strength;
- changing last-10 window or weighting;
- selecting a feature subset;
- adding/removing league effects;
- switching NB2 to Poisson/lognormal-Poisson/COM-Poisson to rescue this run;
- adding chance-state-dependent dispersion after viewing this result;
- adding other market fields;
- changing folds or PASS gates;
- opening confirmation150 to rescue N18C.

The 150 N18B2 confirmation identities remain sealed. They are **not** authorized for N18-D because N18C did not pass.

A future football3 experiment must be a materially new, preregistered hypothesis with a new data plan; it may not be a neighboring repair of N18C on the same consumed development labels.