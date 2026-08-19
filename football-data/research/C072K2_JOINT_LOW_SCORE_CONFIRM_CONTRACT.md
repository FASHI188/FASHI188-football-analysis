# C072-K2 — fresh joint P(T) × P(H|T) low-score integration confirmation

## Lineage / isolation
- football3 only.
- Confirmed P(T) component: C072-F2 candidate = score-history BASE + O/U2.5 opening level + opening→closing movement.
- Confirmed allocation component: C072-I2 candidate = H2 Model A, score-history BASE + opening 1X2 strength/drawness, T=1..6.
- Fresh joint source C072-J2 PASS: EC/T1/G1 2025/26, exactly 1,094 identities, target scores unopened.
- C073-C077 remain quarantined and are not evidence/design/stopping-rule inputs.

## Scientific question
Do the two independently confirmed components combine mechanically into a coherent low-score distribution that improves exact-score probability quality and Top1 on a completely fresh joint pool?

No new predictive model, blend weight, calibration, score boost or class-specific adjustment may be learned in K2.

## Fresh target pool
Football-Data 2025/26 fixed divisions EC, T1, G1, exactly 1,094 identities from C072-J2.
K2 is the first/only authorized opening of their score/result values for this joint-integration question. Missing results are not replaced.

## Frozen training domains
Both component coefficient sets are fitted only on their already-frozen historical nm2890 source through season start-year 2023.
- P(T): exact C072-E2/F2 candidate architecture, 8 classes 0..6,7+.
- P(H|T): exact C072-H2/I2 Model A architecture, separate T=1..6 legal-support models.
- Allocation empirical baseline: pooled alpha=1 P(H|T) from the same historical H2 training domain.

No EC/T1/G1 2024/25 or 2025/26 row may refit coefficients or smoothing.

## Feature-history warmup and PIT
Football-Data 2024/25 EC/T1/G1 scores may be used solely to initialize team/competition result histories after this contract is frozen.
During 2025/26, strictly earlier-date results may update history features for later targets. Same-date matches are all predicted before any same-date result update.
Both teams require >=8 prior result-history matches.

## Frozen market coordinates
P(T):
- de-vig Avg>2.5 / Avg<2.5 -> p_open_over
- de-vig AvgC>2.5 / AvgC<2.5 -> p_close_over
- open_logit = logit(p_open_over)
- movement_logit = logit(p_close_over)-logit(p_open_over)

P(H|T):
- de-vig AvgH/AvgD/AvgA -> (pH,pD,pA)
- open_strength = log(pH/pA)
- open_drawness = log(pD/sqrt(pH*pA))

No closing 1X2 movement is used in allocation because H2 Model B was disqualified.

## Four mechanically defined integration variants
For T=0, H=0 is deterministic.
For T=1..6, joint exact-score probability = P(T=t) × P(H=h|T=t).
For T>=7, retain one aggregate `TAIL_7PLUS` state with probability P(T>=7); do not split it.

Exactly four variants are reported:
1. BASE: P(T) opening-reference × empirical alpha=1 P(H|T).
2. PT_ONLY: confirmed movement P(T) × empirical P(H|T).
3. D_ONLY: P(T) opening-reference × confirmed Model-A P(H|T).
4. BOTH: confirmed movement P(T) × confirmed Model-A P(H|T).

No other combination is permitted.

## Two evaluation spaces
### A. Hybrid 29-state distribution
28 exact-score cells with T<=6 plus one TAIL_7PLUS bucket. Probabilities must sum to 1. All target matches can be scored.
Primary metric: multiclass LogLoss. Secondary: multiclass Brier, Top1, Top3.

### B. Conditional low-score exact distribution
For target matches with realized T<=6, remove the tail bucket and renormalize the 28 exact-score cells to 1.
Metrics: exact-score LogLoss, Brier, Top1, Top3, mean rank of actual score.
Report predicted Top1 frequencies and actual precision/recall for 0-0 and 1-1 explicitly.

RPS is not used because the exact-score cell ordering is not ordinal.

## Frozen identity / coverage gates
Before effect interpretation:
- raw identities reproduce exactly 1,094 and duplicate identity count=0;
- no row replacement;
- pooled eligible hybrid rows >=800;
- pooled eligible T<=6 exact-score rows >=740;
- each of EC/T1/G1 contributes >=150 eligible hybrid rows;
- all required 1X2 and O/U2.5 candidate fields are finite on every scored row;
- all model probability vectors finite/nonnegative and max sum residual <=1e-10.
Failure => STOP_K2_COVERAGE / STOP_K2_IDENTITY_DRIFT.

## Bootstrap / robustness
Paired match bootstrap, 5,000 reps:
- hybrid BOTH-minus-BASE LogLoss seed 72027, 90% interval;
- low-score conditional BOTH-minus-BASE LogLoss seed 72028, 90% interval.

Pre-frozen partitions:
- EC, T1, G1 separately;
- early/late chronological halves.

## All-required K2 PASS gate
`C072K2_JOINT_LOW_SCORE_CONFIRMATION_PASS` only if ALL:
1. identity and coverage gates pass;
2. hybrid BOTH-minus-BASE dLogLoss <0 and bootstrap90 upper<0;
3. hybrid dBrier <=0;
4. low-score conditional BOTH-minus-BASE dLogLoss <0 and bootstrap90 upper<0;
5. low-score conditional dBrier <=0;
6. low-score conditional dTop1 >=0;
7. both early and late hybrid halves have dLogLoss <0;
8. at least 2/3 divisions have hybrid dLogLoss <0;
9. max probability-sum residual <=1e-10.

Top3, 0-0/1-1 diagnostics, or ablation results cannot rescue a failed gate.

## Interpretation boundaries
A PASS confirms integration only for the 29-state hybrid and the T<=6 exact-score subspace. It does NOT close T>=7 exact scores, because 7+ remains an aggregate bucket.
A PASS is research-grade coarse PIT because Football-Data opening/closing rows lack immutable quote timestamps.
formal_weight remains 0.

## One-shot stopping rule
After J2 labels open, no C changes, market transforms, feature changes, tail splitting, score-specific boosts, Draw/0-0/1-1 boosts, league exclusions, recalibration, blending or threshold changes are allowed on this target pool.

## Hard boundaries
- C070-F Confirmation1597 sealed.
- protected sealed.
- C073-C077 quarantined.
- no CURRENT change, formal promotion or full unified exact-score matrix claim.
