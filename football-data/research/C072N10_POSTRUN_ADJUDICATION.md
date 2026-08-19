# C072-N10 — Post-run adjudication

## Binding terminal
`C072N10_MULTILINE_PT_DEVELOPMENT_PARK`

Classification: `REPLICATION / REPRODUCTION` only. This is not independent confirmation, fresh evidence, blind test, or pristine confirmation.

Authoritative GitHub Actions evidence:
- workflow run: `32247583229`
- job: `96051372815`
- artifact: `football3-c072n10-multiline-pt-development`
- artifact id: `9363191491`
- artifact ZIP SHA256: `908cc705f0d4bf883b835b8496e70aadc7a6d5b8af57c21bf80270c1308ec1b9`

## Frozen comparison
Baseline: closing de-vig O/U2.5 logit + league one-hot.

Candidate: closing de-vig O/U0.5/1.5/2.5/3.5/4.5 logits + the same league one-hot.

Same paired rows, same frozen multinomial LogisticRegression `C=0.1`, five expanding chronological OOS seasons 2019/20–2023/24, no search.

## Evidence
Development OOS n: **8,807**.

Pooled baseline:
- LogLoss: `1.8453261199674371`
- Brier: `0.8211386892021807`
- RPS: `0.12620762315911963`
- Top1: `0.25127739298285456`
- Top3: `0.6558419439082548`
- Top1 total=2 fraction: `0.5534234131940502`

Pooled five-line candidate:
- LogLoss: `1.845053379766154`
- Brier: `0.8209881278952393`
- RPS: `0.12615139311753001`
- Top1: `0.25105030089701375`
- Top3: `0.6552742136936528`
- Top1 total=2 fraction: `0.5688656750312252`

Candidate minus baseline:
- dLogLoss: **`-0.00027274020128320586`**
- dBrier: **`-0.00015056130694135472`**
- dRPS: **`-0.00005623004158961109`**
- dTop1: `-0.00022709208584081875`
- dTop3: `-0.0005677302146019914`
- dTop1-total-2 fraction: **`+0.015442261837175009`**

Paired bootstrap dLogLoss, 3000 reps, seed 72009:
- mean: `-0.000272740201282856`
- 90% CI: **`[-0.0010522715435935125, +0.0005135043950529653]`**
- P(delta<0): `0.7363333333333333`

Chronological LogLoss wins: **2/5**.
- 2019/20: `-0.001841736421055451`
- 2020/21: `+0.0004204496792679091`
- 2021/22: `+0.0006660183576805423`
- 2022/23: `+0.0011057008035650906`
- 2023/24: `-0.0018929275466872397`

League LogLoss wins: **3/5**.
- Bundesliga: `+0.0019528154345662685`
- EPL: `-0.0016889643381146069`
- Ligue 1: `-0.001085294895550204`
- LaLiga: `+0.0006279021742026103`
- Serie A: `-0.0007843441907848803`

## Gate adjudication
PASS gates that succeeded:
- pooled dLogLoss < 0
- pooled dBrier <= 0
- pooled dRPS <= 0
- five folds executed
- probability conservation
- 2024/25 target values read = 0
- 2025/26 target values read = 0

PASS gates that failed:
- bootstrap90 upper dLogLoss < 0
- >=4/5 chronological LogLoss wins
- >=4/5 league LogLoss wins
- pooled Top1 nonworse
- pooled Top3 nonworse

Therefore the candidate does **not** establish a stable incremental improvement in match-level `P(T)` over O/U2.5 alone.

## Interpretation / stopping rule
The five-line closing curve has a very small favorable pooled proper-score point estimate, but the effect is unstable across time and leagues and the bootstrap interval crosses zero. It also slightly worsens Top1/Top3 and increases the fraction of T=2 Top1 calls by about 1.54 percentage points.

This result parks **this exact representation**: raw five closing-line de-vig logits fed linearly to the frozen multinomial logistic model. It does not prove that all multi-line O/U information is useless.

No post-view repair is allowed on these labels: do not change C, choose a subset of lines, add curvature/interactions, change model family, change folds, subset leagues, or tune thresholds and call it the same experiment.

A new hypothesis must be preregistered and evaluated on a new globally unconsumed data plan. The scientifically preferred next axis remains genuinely timestamped/dynamic multi-time O/U probability-surface information rather than retuning this consumed closing-snapshot experiment.

## Boundaries
- C073-C077 remain quarantined as scientific guidance.
- C070-F Confirmation1597 remains sealed.
- protected assets remain sealed.
- 2024/25 and 2025/26 targets were not read by N10.
- formal_weight=0; no CURRENT promotion.
