# C072-L2 — post-view Top1 mechanism audit

## Status
Diagnostic only, after C072-K2 fresh joint confirmation PASS. This is **not** new scientific confirmation and may not be used to tune K2 on its consumed EC/T1/G1 labels.

C073-C077 remain quarantined. C070-F Confirmation1597 and protected samples remain sealed.

## Questions frozen before diagnostic execution
Using the exact frozen K2 models and the already-consumed K2 target rows, measure only:
1. How often the P(T) opening reference and confirmed movement candidate call each total class as Top1, especially T=2.
2. On rows where the BOTH joint model calls 1-1 Top1, how often P(T) itself calls T=2 Top1.
3. For the frozen Model-A allocation at T=2, how often H=1 is the conditional Top1 allocation and its mean probability versus H=0/H=2.
4. For BOTH 1-1 Top1 calls, the probability margin over the second-best exact score: median, p10, p25, p75, p90, and shares with margin <0.005, <0.01, <0.02, <0.05.
5. Compare 1-1 Top1 probability and runner-up identity distributions.
6. Report P(T=0), P(T=1), P(T=2), P(T=3) mean/median and actual total frequencies on K2 eligible rows.
7. Report exact-score Top1 call entropy / effective number of called score classes for BASE, PT_ONLY, D_ONLY, BOTH as a descriptive concentration diagnostic.

## Hard boundaries
- No model fit changes.
- No C/feature/transform/weight/calibration changes.
- No score-specific or Draw/0-0/1-1 boost.
- No selection of league subsets or threshold search.
- No PASS/FAIL scientific gate; terminal is only `POSTVIEW_DIAGNOSTIC_COMPLETE` or engineering failure.
- Diagnostic may motivate a future genuinely new preregistered P(T) information experiment, but cannot repair or relabel K2.
