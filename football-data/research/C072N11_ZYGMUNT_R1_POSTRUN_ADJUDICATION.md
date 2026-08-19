# C072-N11 zygmunt R1 — post-run adjudication

Project: football3 only
Parent: C072-N10 static closing multi-line P(T) development, terminal PARK
Stage: C072-N11 zero-label dynamic multi-line O/U source feasibility

Official run: 32250238925
Official job: 96059372292
Artifact: `football3-c072n11-zygmunt-r1-identity`
Artifact id: 9364126906
Artifact digest: `sha256:39454166267e20227726c3985e93e29b8f95c49dd7d0174048f2d6c77199afcb`
Source: Kaggle `zygmunt/betfair-sports`, file `betfair_140901.csv`
Source file SHA256: `ce72ba2ebdc79bf22b169f32fa279a4adee7ef5b6b946ae87e2a39decb291fb4`

## Terminal
`ZYGMUNT_R1_MULTILINE_STRUCTURE_PASS`

This is a **zero-label source-structure PASS only**. It is not a model PASS, scientific PASS, confirmation PASS, or breakthrough claim. It authorizes no target access.

## Exact source/identity results
- rows scanned: 1,306,748
- exact full-match `Over/Under N.5 Goals` rows: 57,615
- clean exact O/U line markets: 3,751
- reconstructed matches: 738
- matches with >=2 preferred lines at any prematch time: 613
- matches with >=3 preferred lines at any prematch time: 549
- matches with all five preferred lines at any prematch time: 387
- duplicate reconstructed match-line groups containing multiple source EVENT_IDs: 135
- normalized base descriptions with multiple SCHEDULED_OFF values: 0

Preferred per-line reconstructed match counts:
- O/U0.5: 549
- O/U1.5: 522
- O/U2.5: 637
- O/U3.5: 542
- O/U4.5: 515

## Frozen-cutoff dynamic coverage
Strict-identifiable multi-line coverage:
- T-24h: >=2 lines 1; >=3 lines 0; all five 0
- T-6h: >=2 lines 14; >=3 lines 3; all five 0
- T-1h: >=2 lines 29; >=3 lines 1; all five 0

PIT-safe latest-completed-price-level proxy coverage:
- T-24h: >=2 lines 17; >=3 lines 4; all five 1
- T-6h: >=2 lines 133; >=3 lines 69; all five 7
- T-1h: >=2 lines 373; >=3 lines 249; all five 42

Same preferred lines present at both T-6h and T-1h:
- strict >=2: 4
- strict >=3: 0
- proxy >=2: 133
- proxy >=3: 69

## Boundary audit
- forbidden outcome/settlement field values materialized: 0
- winner/result values materialized: 0
- model fits: 0
- C073-C077 scientific results used: false
- C070-F Confirmation1597 opened: false

## Interpretation
The source establishes that genuinely dynamic multi-line full-match O/U structure exists in a public football dataset and can be reconstructed without target access. The source uses legacy Betfair price-level FIRST_TAKEN/LATEST_TAKEN intervals rather than exact Exchange Stream snapshots, so latest-completed-level observations are PIT-safe proxies, not exact contemporaneous LTP snapshots.

The usable joint dynamic sample is too small and temporally concentrated to support a robust football3 breakthrough claim. No outcome labels are to be opened merely because this structure gate passed.

## Stopping / continuation rule
1. Do not score or tune on this source from N11.
2. Continue zero-label source expansion first, preferring a substantially larger timestamped O/U domain with exact or stronger PIT semantics.
3. If a later pilot is ever authorized on this one-week source, freeze a separate scientific contract before any settlement/outcome access and label the result as a pilot, not confirmation.
4. Do not modify N10 C/window/line subset/model using viewed N10 labels.
