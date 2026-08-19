# FOOTBALL3 INDEPENDENT CURRENT

Updated: 2026-08-19

## Isolation rule
This file is the continuation checkpoint for the **football3 independent line** recovered from C072-C.

C073-C077 from the later football-project branch are quarantined. They are not football3 continuation state, evidence, model-selection input, stopping-rule input, or scientific ancestry.

## Parent breakpoint
C072-C @ `e3e73c998020beef585cc459a69ea5b73b44ddb3`.

## Independently established after recovery
1. C072-D2 free O/U2.5 opening/closing source gate PASS (zero label).
2. C072-E2 P(T) movement development PASS.
3. C072-F2 2024/25 P(T) forward confirmation PASS.
4. C072-G2 fresh 2025/26 12-division D|T source gate PASS (zero label).
5. C072-H2 D|T development selected Model A = score-history + opening 1X2; closing movement disqualified by frozen Top1 gate.
6. C072-I2 fresh 2025/26 external D|T confirmation PASS.
7. C072-J2 fresh EC/T1/G1 joint source gate PASS (zero label).
8. C072-K2 fresh joint integration PASS on 29-state hybrid (28 T<=6 exact scores + 7+ bucket) and conditional 28-score T<=6 space.
9. C072-L2 post-view mechanism diagnostic COMPLETE; no tuning.

## Key confirmed numbers
### P(T), C072-F2
753 forward rows; candidate vs score-history+opening reference: dLogLoss=-0.0147131; bootstrap90 [-0.0232912,-0.00647973]; dBrier=-0.00301955; dRPS=-0.00208562; Top1 +0.531pp.

### P(H|T), T=1..6, C072-I2
3461 external rows; dLogLoss=-0.0681140; bootstrap90 [-0.0774020,-0.0587301]; dBrier=-0.0311103; dRPS=-0.0157929; Top1 +1.936pp; 11/12 divisions and 6/6 exact-T groups improve.

### Joint K2
1006 hybrid rows / 990 T<=6 rows. BOTH vs BASE:
- hybrid dLogLoss=-0.110469, bootstrap90 [-0.132003,-0.089197], dBrier=-0.009349, Top1 +0.398pp, Top3 +4.672pp;
- conditional 28-score dLogLoss=-0.111094, bootstrap90 [-0.133051,-0.088983], dBrier=-0.009591, Top1 +0.404pp, Top3 +4.646pp.
All 3 divisions and both time halves improve.

## Current unresolved Top1 mechanism
K2 baseline called 1-1 Top1 on 98.59% of T<=6 rows. BOTH reduces this to 65.05%, so the collapse is materially reduced but not closed.

C072-L2 diagnostic identifies the primary remaining bottleneck as **P(T) match-level resolution**:
- current P(T) candidate calls T=2 Top1 on 807/1006 = 80.22% of eligible rows;
- actual T=2 is only 236/1006 = 23.46%; actual T=3 is more common (261 rows);
- 91.61% of BOTH 1-1 Top1 calls occur when P(T) itself calls T=2 Top1;
- the D|T component already expands exact-score effective called classes from ~1.08 (BASE) to ~3.23 (D_ONLY), while PT_ONLY remains ~1.10;
- Model-A T=2 conditional allocation still calls H=1 Top1 on 73.16%, so allocation is not perfect, but it is no longer the main concentration source.

## Scientific interpretation
Low-score factorization is now independently viable at proper-score level, but exact-score Top1 remains too concentrated. Artificial Draw/0-0/1-1 boosts are not justified. The next scientific target is new information that resolves **total-goal shape**, ideally multiple O/U thresholds (1.5/2.5/3.5/...) or another orthogonal pre-match goal-shape market, rather than further tuning the single O/U2.5 representation.

## Hard boundaries
- C070-F Confirmation1597 remains sealed.
- protected samples remain sealed.
- T>=7 exact scores remain unresolved; 7+ is still an aggregate bucket.
- formal_weight=0; no formal unified exact-score matrix.
- K2/L2 consumed EC/T1/G1 labels cannot be used to tune a repair.

## Unique next research step
Zero-label audit a no-registration source for **multiple pre-match O/U goal lines**. Freeze its source/coverage/PIT contract before any historical target result is parsed. If a viable source exists, only then preregister a new P(T) multi-line development experiment on a non-confirmation domain and reserve a separate fresh confirmation pool.
