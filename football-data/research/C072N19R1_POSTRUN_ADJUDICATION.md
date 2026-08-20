# C072-N19R1 — post-run adjudication

## Binding terminal

`C072N19R1_REPLICATION_PARK`

Classification: `REPLICATION / REPRODUCTION ONLY`; not fresh, blind, pristine, or independent confirmation. `formal_weight=0`.

## Authoritative execution

- workflow run: `32324047869`
- job: `96291595833`
- head: `68296307ff70090b5efacb6b76a919ff08130b7e`
- artifact: `football3-c072n19r1-replication1000-68296307ff70090b5efacb6b76a919ff08130b7e-32324047869`
- artifact id: `9390720861`
- artifact digest: `sha256:49b70fcf7f1bd472b4138e7a75f591d02edfc3cad5d6085c158b23edc6c3780c`

## Boundary audit

- frozen identity count reproduced: `1000`
- frozen identity SHA reproduced: `10f77a6b20502813c0ae8402c7dd45e80054dcc5b6b6546751fca736033cddce`
- target rows materialized: `1000`
- target values materialized: `2000` (`FTHG`,`FTAG` only for frozen identities)
- chronological OOS: `600`
- C070-F Confirmation1597 opened: false
- N17 reserve266 opened: false
- N18 confirmation150 opened: false
- C073-C077 scientific results used for design/tuning/stopping: false

The 1000 N19R1 identities are now globally consumed and may not be reused for a neighboring movement transform, lambda, subset, family, or gate search.

## Frozen comparison

B0 closing anchor:
`log(mu)=log(mu_market_close)+beta0`, full-support NB2.

C movement:
`log(mu)=log(mu_market_close)+beta0+gamma*z(movement_logit)`, same full-support NB2; movement standardized within training fold; fixed L2 lambda=1 on gamma only.

`movement_logit = logit(q_over_close)-logit(q_over_open)`.

Four chronological folds: 400/150, 550/150, 700/150, 850/150; pooled OOS=600.

## Pooled evidence

Baseline:
- LogLoss `1.8249817611891819`
- Brier `0.8204342439736769`
- RPS `0.12407452718976764`
- Top1 `0.225`
- Top3 `0.6516666666666666`

Candidate:
- LogLoss `1.8251362375089388`
- Brier `0.8203733186565119`
- RPS `0.12408212356361963`
- Top1 `0.22333333333333333`
- Top3 `0.6516666666666666`

Candidate minus baseline:
- dLogLoss `+0.00015447631975695764` — worse
- dBrier `-0.00006092531716506144` — tiny improvement
- dRPS `+0.000007596373851989635` — worse
- dTop1 `-0.0016666666666666774`
- dTop3 `0`

Paired bootstrap 5000 seed72019:
- 90% CI dLogLoss `[-0.0005641320617673596,+0.0008987144239367977]`
- bootstrap mean dLogLoss `+0.0001587316888851402`
- P(dLogLoss<0) `0.3646`

Chronological LogLoss wins: `3/4`, but all improvements are tiny and the third fold deteriorates by `+0.0017805457300776872`.

Source-code LogLoss wins: `5/10`.

Winning source codes: E1, F2, I2, SP1, SP2.
Losing source codes: D1, D2, E0, F1, I1.

Strong replication screen: false.

## Scientific interpretation

The preregistered conditional question is answered negatively on this replication domain: **opening→closing O/U2.5 movement does not establish incremental full-P(T) information once closing O/U2.5 is already supplied as the market anchor.**

This reconciles the earlier football3 D3/E2 development evidence rather than contradicting it. In E2 the reference contained the opening O/U2.5 level and the candidate added `movement = close-open`; algebraically, opening plus movement exposes the closing level. Therefore E2's stable gain can be explained by the information arriving in the final closing level, not by an independently useful path/trajectory effect conditional on that close.

D3 likewise established that movement is predictive relative to score-history-only information, but did not condition on the final closing O/U2.5 anchor. N19R1 specifically performs that missing conditional test and finds no stable proper-score increment.

Accordingly, the coarse single-line open→close movement route is PARKed for football3 when closing O/U2.5 is available. It must not be repaired on these 1000 labels.

## Research implication

For the football3 P(T) main route, the next useful information source must be orthogonal to the final single-line total-intensity level itself. Repeating same-line movement, changing its transform, adding thresholds, or reweighting it is not authorized by this evidence.

This adjudication does not open any sealed confirmation pool and does not authorize Draw/0-0/1-1 manual adjustment.
