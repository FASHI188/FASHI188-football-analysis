# C072-N13 — Post-run adjudication

## Binding terminal
`C072N13_ALEAGUE_DYNAMIC_MULTILINE_PT_PARK`

Project: football3 only.
Classification: DEVELOPMENT EVIDENCE, NOT CONFIRMATION.
formal_weight=0; no CURRENT promotion.

Authoritative GitHub Actions evidence:
- run: `32257520960`
- job: `96082610447`
- artifact: `football3-c072n13-aleague-dynamic-multiline-pt`
- artifact id: `9366892752`
- artifact ZIP SHA256: `35b9cbdea2d371b8aec6df69ec105309e42d6862d276f43472b79ac423f5b4ea`

## Boundary result
- exact zero-label five-season all-five/all-three eligibility reproduced: 125/156/159/168/174 = 782 events;
- target values were read only for already-eligible development EVENT_ID rows;
- valid materialized development targets: 779;
- pooled chronological OOS rows: 655;
- 2025-2026 file downloaded: false;
- 2025-2026 target values read: 0;
- C073-C077 scientific results used: false;
- C070-F Confirmation1597 opened: false;
- protected assets opened: false.

## Frozen comparison
B0 static single-line:
- O/U2.5 de-vig midpoint logit at T-1min.

B1 strong dynamic single-line:
- O/U2.5 logits at T-60/T-30/T-1.

C dynamic multi-line surface:
- all fifteen O/U logits from lines 0.5/1.5/2.5/3.5/4.5 × T-60/T-30/T-1.

Identical multinomial LogisticRegression `C=0.1`, no search, four expanding chronological season folds.

## Pooled evidence
B0:
- LogLoss `1.941527547667571`
- Brier `0.8425297707224766`
- RPS `0.1360704761338801`
- Top1 `0.20916030534351146`
- Top3 `0.6152671755725191`

B1:
- LogLoss `1.9459988520414349`
- Brier `0.8441390394514787`
- RPS `0.1360312565252043`
- Top1 `0.2183206106870229`
- Top3 `0.6`

C:
- LogLoss `1.9595419369364862`
- Brier `0.8464106072807653`
- RPS `0.1362573201648029`
- Top1 `0.20610687022900764`
- Top3 `0.5816793893129771`

### C minus B1 — primary
- dLogLoss `+0.013543084895051294` (worse)
- dBrier `+0.0022715678292866137`
- dRPS `+0.00022606363959859488`
- dTop1 `-0.012213740458015265`
- dTop3 `-0.018320610687022842`
- chronological LL wins: `1/4`
- bootstrap 3000 seed 72015: 90% CI `[+0.006101462684286444,+0.021313922547409964]`, P(delta<0)=`0.0013333333333333333`.

### C minus B0
- dLogLoss `+0.018014389268915076` (worse)
- dBrier `+0.003880836558288747`
- dRPS `+0.00018684403092281143`
- dTop1 `-0.0030534351145038163`
- dTop3 `-0.03358778625954195`
- chronological LL wins: `1/4`
- bootstrap 90% CI `[+0.006338129324956278,+0.029671647639487]`.

### B1 minus B0
- dLogLoss `+0.0044713043738637825`
- dBrier `+0.0016092687290021335`
- dRPS `-0.000039219608675783446`
- dTop1 `+0.009160305343511449`
- dTop3 `-0.01526717557251911`
- chronological LL wins: `1/4`
- bootstrap 90% CI `[-0.0015204685366337907,+0.010896148323969984]`.

## Scientific interpretation / stopping rule
The exact raw 15-logit linear multinomial representation is rejected on this development domain. Its failure is not a marginal miss: primary LogLoss, Brier, RPS and Top-k all move in the wrong direction, and the paired bootstrap interval for C-vs-B1 is wholly above zero.

This does **not** establish that a multi-line total-goal probability surface is useless. The five half-goal O/U lines are themselves cumulative tail probabilities of the total-goal distribution, so a structurally constrained distribution reconstruction is a scientifically distinct post-view hypothesis from feeding fifteen highly correlated logits into an unconstrained linear multiclass model.

N13 labels are now globally consumed. No feature/model/C/line/window repair may be attempted on these labels.

A structurally constrained probability-surface hypothesis may proceed only under a separately frozen post-view contract on a new data plan/domain. 2025-2026 men's A-League remains sealed and is not authorized for N13 repair.

## Boundaries
- 2025-2026 men's A-League remains target-sealed.
- C070-F Confirmation1597 remains sealed.
- C073-C077 remain scientifically quarantined.
- No Draw/0-0/1-1/T=2 manual adjustment is authorized.
