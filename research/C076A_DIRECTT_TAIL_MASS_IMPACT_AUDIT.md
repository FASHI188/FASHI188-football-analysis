# C076-A Frozen Contract — Direct-T 7+ Mass / Identifiability Impact Audit

Status: research/governance audit only; `formal_weight=0`; no new predictive family, no tuning, no promotion claim.

## Question
Given the already-confirmed C074-G Direct-T candidate, how much probability mass is typically carried by the public `7+` class on its already-viewed 2025/26 forward confirmation domain, and therefore what is the maximum probability impact of not knowing the internal exact-tail decomposition?

## Data/model reuse
- Re-run the exact frozen C074-G source, parsing, historical features, train/test split and candidate model unchanged.
- Same seven 2025/26 confirmation leagues and same 2019/20–2024/25 training range.
- No new data source, no new target rows, no feature/C/subset/transform/fold search.
- All 2025/26 labels in this domain were already consumed by C074-G. This audit is descriptive only.

## Required outputs
1. Candidate `P(T>=7)` distribution on every C074-G confirmation row: mean, min, p10, p25, p50, p75, p90, p95, p99, max.
2. Fractions with `P(T>=7)` below 0.5%, 1%, 2%, 3%, 5%, and 10%.
3. Same quantities for the C074-G baseline for context.
4. Actual observed `T>=7` rate as a descriptive calibration reference only.
5. Per-league candidate mean/p50/p90/p95/max `P(T>=7)` and actual `T>=7` count/rate.
6. Mathematical identifiability statements, mechanically encoded:
   - for any binary event whose settlement can differ across exact scores inside `T>=7`, the unknown internal tail can change the event probability by at most `P(T>=7)`;
   - for any event that is constant for all exact scores with `T>=7`, internal tail decomposition contributes zero uncertainty;
   - O/U thresholds up through 6.5 are constant on the 7+ set, so their probability is not affected by exact-tail decomposition;
   - O/U 7.0, 7.5 and higher, exact total 7/8/..., scoreline, BTTS, H/D/A, AH and score-dependent EV can in principle depend on the internal tail;
   - without an upper support or validated tail law, exact expected total from the 7+ bucket is not finitely upper-bounded by the public 8-class vector alone.

## Boundary
This audit cannot override CURRENT V5.2 exact-tail hard gates. Even if `P(T>=7)` is numerically small, no low-tail-mass exception is created and no formal exact-score matrix may be generated.

Protected/sealed assets remain untouched: C071 reserve52,180; C070-F Confirmation1,597; A05; protected.
