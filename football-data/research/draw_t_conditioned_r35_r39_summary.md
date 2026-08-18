# Draw T-conditioned research R35-R39 summary

Status: research-only; no formal promotion; formal_weight=0.

## Core structural hypothesis
Model draw through the formal structural chain rather than direct Draw/Not-Draw classification:
1. Direct total-goals track predicts T in 0..7+.
2. Conditional draw track estimates P(D=0 | T, X), with T=0 deterministic draw and odd T impossible.
3. Final draw probability is aggregated from the T distribution and conditional D=0 probabilities.
4. Home/away residual mass preserves market H:A ratio.
5. Strict-prior team and league features are built with shift(1)/expanding or rolling windows; no target-match result is included in its own features.

## R35 — 300-match preregistered PASS
Window: 2016-08-15..2016-08-21; test_n=300; train_n=34,202; conditional train_n=15,273.
- HDA LogLoss: 0.9737682 -> 0.9693517 (delta -0.0044166)
- Draw LogLoss: 0.5447688 -> 0.5403522
- Draw AUC: 0.6049725 -> 0.6198178 (delta +0.0148452)
- Multiclass Brier delta: -0.0031654
- 90% calendar-date block bootstrap CI: [-0.0085493, -0.0019548]
- probability conservation max abs residual: 2.22e-16
- preregistered core gate: PASS
- direct-T Top1 total-goals accuracy: 29.0%

## R36 — 300-match directional improvement, gate FAIL
Window: 2016-08-22..2016-08-28; test_n=300.
- HDA LL delta -0.0041018
- Draw AUC delta +0.0028750
- Brier delta -0.0023613
- bootstrap CI [-0.0072680, +0.0009946]
- core gate FAIL because CI crosses zero.

## R37 — 300-match small directional improvement, gate FAIL
Window: 2016-09-05..2016-09-11; test_n=300.
- HDA LL delta -0.0015684
- Draw AUC delta +0.0020704
- Brier delta -0.0005120
- bootstrap CI [-0.0041455, +0.0071918]
- core gate FAIL.

## R38 — 1000-match larger confirmation, positive mean but not statistically hard
Window: 2016-09-12..2016-10-23; test_n=1000.
- HDA LL delta -0.0017259
- Draw LogLoss delta -0.0017259
- Draw AUC delta +0.0115026
- Brier delta -0.0013325
- bootstrap CI [-0.0051446, +0.0012101]
- core gate FAIL because CI crosses zero.
- candidate Top1 Draw count 9; hits 0, so probability improvement did not translate to reliable argmax draw execution.

## R39 — 1000-match selective Top1 attempt FAIL
Window: 2016-10-24..2016-11-19; test_n=1000.
- probability layer: HDA LL delta +0.0005393; Draw AUC delta -0.0015597; probability guard FAIL.
- selector: top30 (3% coverage), 7 hits, precision 23.33%, recall 2.78%.
- combined 1X2 accuracy delta vs market -0.8pp.
- selector gate FAIL.

## Scientific ruling
1. Major positive discovery: the formal Direct-T -> conditional D=0|T structural chain is empirically viable. R35 is a clean preregistered PASS, and R36-R38 keep the mean effect mostly in the same direction, including a 1000-match positive mean effect in R38.
2. Robustness is not yet sufficient for formal promotion: R36, R37 and R38 fail the hard bootstrap gate.
3. Top1 draw execution remains unsolved. R38/R39 show that better draw probabilities do not automatically create reliable Top1 draw calls.
4. Do not tune on R35-R39 after observing outcomes. Any next step must use a fresh untouched window or a newly preregistered execution family.
5. Preserve R23/R24 evidence separately: R24 mature-history transition model PASS remains valid research evidence; these R35-R39 experiments are a separate T-conditioned structural line.

## Integrity hashes
- R35 result SHA256: 4d8a28e08d4b08f8c1341dcc9186d3190b2f24ad1460b42e15af36093d98af93
- R36 result SHA256: dfa454718e5a620088df9c1f285a2fc5e89b9cccce70340bf5aac898a0db9b04
- R37 result SHA256: f54a3e47b9f0aca6bf7f9aae214e13417111ac6a17db726312d4e66d910b25a6
- R38 result SHA256: 5e61b5d2d6fc4115826c7ce40020e25932796ff0f257972c6b5adb1169d0da27
- R39 result SHA256: cbffc397ea85bb03e2ec9146940172ad07d07281346a2adfdfc391e4d101a3bb
- R35 scorer SHA256: 95736ba9085a550f3aeac44ee7e522a978cc503d4e5b1722e9412b4468aa33b0
- R39 scorer SHA256: ebe73be6a81a666bfd254bd9bd757351849dd9ca80981ee94e919ad1517a06c0
