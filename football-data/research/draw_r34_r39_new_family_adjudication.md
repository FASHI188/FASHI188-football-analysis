# Draw research R34-R39 new-family adjudication

Research-only. `formal_weight=0`. No formal model/config/CURRENT change. R45B prospective 2/300 untouched.

## R34 — 32-book market microstructure — FAIL
Fresh 300, prereg SHA `870120ec0dd6847fb1a5f6f84c7ac660c02f27ee3db65e5b7cf57eb92a63af58`.
- HDA LL: 0.9922664 -> 1.0001355, delta +0.0078691
- Draw AUC: 0.5265231 -> 0.4913388
- Brier delta +0.0043230
- date-block bootstrap90 LL delta [0.0004084, 0.0168185]
- core gate FAIL; family sealed without retuning.

## R35-R38 — Direct-T -> conditional D=0 structural chain
Frozen chain: predict P(T=0,1,2,3,4,5,6,7+), then P(D=0|T,X); T=0 draw deterministic, odd T draw impossible, even/T7+ conditional model learned. No manual draw bonus. H/A residual preserves market H:A ratio.

### R35 fresh 300 — PASS
Prereg `c5be67bf09d73a68d6771937f993476d76969502d4c16dc13f20ef34442687bf`.
- HDA LL delta -0.0044166
- Draw AUC delta +0.0148452
- Brier delta -0.0031654
- bootstrap90 [-0.0085493, -0.0019548]
- core PASS

### R36 fresh 300 — directional near-pass, core FAIL
Prereg `485f35aad57103b4f044c01c706decb531a709796134bdbe9a8c964be6510150`.
- LL delta -0.0041018
- AUC delta +0.0028750
- Brier delta -0.0023613
- bootstrap90 [-0.0072680, +0.0009946]

### R37 fresh 300 — directional near-pass, core FAIL
Prereg `bd513f8ea2f4c3d3098b9a35e31cf3e8ce485d5d5219c028ad3fd351ed4ba3a0`.
- LL delta -0.0015684
- AUC delta +0.0020704
- Brier delta -0.0005120
- bootstrap90 [-0.0041455, +0.0071918]

### R38 fresh 1000 — directional but core FAIL
Prereg `47cee6acbc8ddf2cf95f5ce62183b38bcc66b0cd2f52d350d2d558bc6b8517ea`.
- LL delta -0.0017259
- AUC delta +0.0115026
- Brier delta -0.0013325
- bootstrap90 [-0.0051446, +0.0012101]

### Pooled diagnostic R35-R38 (1900 non-overlapping matches)
Same frozen algorithm across all windows; pooled audit was computed after the individual results and is therefore diagnostic, not a preregistered promotion gate.
- HDA LL delta -0.0025010
- Draw AUC delta +0.0092975
- Brier delta -0.0016548
- calendar-date bootstrap90 [-0.0044489, -0.0005596]
- probability signal is directionally replicated, but natural Top-1 Draw over these 1900 was only 14 selections / 2 hits.

## R39 — preregistered T-conditioned high-confidence selector — FAIL
Fresh 1000; prereg `f05f020bc99d1fbd0968f4a265973998b87e3477ef825b9ef8c32f3cd2a1170f`.
Frozen execution: rank `qD / market_pD`, force Draw for exactly top30 (3%); all others market argmax.
- selected 30, hits 7
- precision 23.33%
- recall 2.78%
- combined accuracy delta -0.80pp
- probability layer in this later window also weakened: LL delta +0.0005393; AUC delta -0.0015597
- selector and probability guard both FAIL
- result SHA `cbffc397ea85bb03e2ec9146940172ad07d07281346a2adfdfc391e4d101a3bb`

## Ruling
`T_CONDITIONED_DRAW_PROBABILITY_SIGNAL_POOLED_REPLICATED_BUT_NONSTATIONARY_TOP1_NOT_VALIDATED`

The structural T-conditioned chain is materially better than direct Draw/Not-Draw forcing as a probability model and produced consistent directional gains in R35-R38, with a significant pooled diagnostic. However the gain is not stationary across all later windows and does not yield a reproducible Top-1/high-confidence draw execution rule. Do not tune R34-R39 samples after seeing results. Any further Top-1 work needs genuinely richer PIT information rather than another selector on this archive.
