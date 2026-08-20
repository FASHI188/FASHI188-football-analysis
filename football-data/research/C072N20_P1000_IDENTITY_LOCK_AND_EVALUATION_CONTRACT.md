# C072-N20 — exact P1000 identity lock + one-shot evaluation contract

Frozen before any N20 target access. Classification: POST-VIEW DEVELOPMENT / REPLICATION PILOT; never fresh/pristine/blind/independent confirmation. formal_weight=0.

## Zero-label lock receipt
- source run: `32326026137`, job `96297327223` — SUCCESS.
- source artifact: `9391374469`, digest `sha256:f5c66cf6d9790384dc688232962f56c939212f9b15809236f3f1697dab330213`.
- full N16R1 inventory: 14,250 rows reproduced.
- prior football3 N16R1 selected cohort: 2,000 rows, ordered identity SHA `65491bb169bc1257ac802970a9e235324b55085863ba53fdf6c84a74b275a559` reproduced.
- cross-project global-consumption exclusion only: C079 consumed 1,000 identity SHA `ce2af86f206077255ea489242a3e8473e34b89f140cc9528f2ad9594593c3413` reproduced.
- complete rows before exclusions: 13,510.
- eligible after exclusions: 11,542.
- N20 selected rows: exactly 1,000.
- **N20 ordered identity SHA: `a49e61df94d0f9c368b314829901f0d64d69ad25c51813551a298307e15e56cf`.**
- source counts: BR 318 / GR 121 / MLS 313 / TR 248.
- overlap old football3 selected2000: 0.
- overlap C079 consumed1000: 0.
- duplicate selected identities: 0.
- all-five two-sided O/U coverage: 100%.
- N20 target/result values materialized before this contract: 0; model fit/scoring: 0/0.
- C070-F1597 / N17 reserve266 / N18 confirmation150 opened: false/false/false.

No identity replacement, season/league subset change or coverage relaxation is permitted after this lock.

## Training/calibration data
Training is not fresh evidence. Use only:
1. N16R1 old selected2000 zero-label odds artifact `9368768296`;
2. N17 authoritative already-consumed development join artifact `9370213104`, exactly 1,734 rows with FTHG/FTAG;
3. intersect the N17 1,734 rows with the N16R1 odds by `identity_sha256` one-to-one.

The N17 reserve266 must not be opened, joined or used.

## Market probabilities
For each O/U line L in {0.5,1.5,2.5,3.5,4.5}, two-sided de-vig:
`q_L=(1/O_L)/((1/O_L)+(1/U_L))`, clipped to [1e-6,1-1e-6] only for numeric logit safety.

Threshold mapping:
- 0.5 -> Y1=1[T>=1]
- 1.5 -> Y2=1[T>=2]
- 2.5 -> Y3=1[T>=3]
- 3.5 -> Y4=1[T>=4]
- 4.5 -> Y5=1[T>=5]

## Frozen line-specific measurement calibration
For each k=1..5 independently, on the fixed already-consumed training rows with a valid corresponding O/U pair, fit exactly:
`logit P(Yk=1) = a_k + b_k * logit(q_k)`
using sklearn `LogisticRegression(C=1.0, penalty='l2', solver='lbfgs', max_iter=2000, class_weight=None, random_state=0)`.
No league/team/time interactions, no alternate C, no isotonic-vs-logistic comparison and no post-view recalibration.

## Baseline B0
Use only calibrated q3 from O/U2.5.
For each test match, solve unique Poisson mu in [0.05,10] satisfying `P_Pois(mu)(T>=3)=q3_cal`.
Construct full Poisson probabilities and collapse to classes 0..6,7+.

## Candidate C — latent market-measurement reconstruction
1. Compute calibrated q1..q5 using the five frozen line-specific calibrators.
2. Project q1..q5 to the closest non-increasing sequence by deterministic equal-weight PAVA; no target information enters projection.
3. Let `q5*` be projected P(T>=5).
4. From the same B0 Poisson mu, compute fixed continuation ratios `r6=P_Pois(T>=6)/P_Pois(T>=5)` and `r7=P_Pois(T>=7)/P_Pois(T>=5)`.
5. Candidate tails: q6=`q5* * r6`, q7=`q5* * r7`.
6. Candidate exact classes:
   - p0=1-q1
   - p1=q1-q2
   - p2=q2-q3
   - p3=q3-q4
   - p4=q4-q5
   - p5=q5-q6
   - p6=q6-q7
   - p7+=q7.
7. Numerical cleanup only: values within 1e-12 of zero may be clipped to zero; otherwise negative mass is a hard failure. Probability sum residual must be <=1e-10.

This candidate is deliberately neither the N10 ordinary-feature multinomial mapping nor the N15W raw/noiseless-CDF reconstruction.

## N20 target access boundary
Only the exact locked N20 1000 may have FTHG/FTAG numerically decoded. Target source is Footiqo Overview tables for BR/GR/MLS/TR.
- server transport of non-selected rows is allowed only as unavoidable table response transport;
- code must test sourceCode+id against the selected set before normalizing/storing FTHG/FTAG;
- FTR/HT results/corners/cards/other stats are forbidden;
- require exact 1000/1000 identity+score join; any shortfall is STOP_COVERAGE before scientific adjudication.

## Frozen metrics
Primary: multiclass LogLoss on T=min(FTHG+FTAG,7), candidate minus B0.
Secondary proper scores: multiclass Brier and normalized RPS.
Diagnostics only: Top1, Top3, T=2 Top1 fraction, mean entropy, per-source dLogLoss.
Paired bootstrap: 5,000 match resamples, seed `72020`, 90% CI of dLogLoss.

## Frozen pilot gate
`C072N20_P1000_PILOT_SIGNAL` requires ALL:
1. target join exactly 1000/1000 and identity SHA reproduced;
2. pooled dLogLoss < 0;
3. bootstrap90 upper dLogLoss < 0;
4. dBrier <= 0;
5. dRPS <= 0;
6. at least 3/4 of BR/GR/MLS/TR have dLogLoss < 0;
7. probability conservation residual <=1e-10;
8. C070-F1597, N17 reserve266, N18 confirmation150 target reads remain 0.

Top1/Top3/Draw/score-call counts cannot override proper-score gates.

Anything else is `C072N20_P1000_PILOT_NO_SIGNAL`, except engineering/coverage STOP.

## Stopping and evidence boundary
After the N20 1000 labels are viewed, this exact hypothesis is frozen. No changing calibration C, calibration family, PAVA weights, continuation law, line subset, source subset, target subset, bootstrap seed, metrics or gates on the same 1000.

If PILOT_SIGNAL: only estimate effect size/uncertainty and design a separate power/precision-based globally appropriate confirmation plan; no confirmation label may be opened automatically.
If PILOT_NO_SIGNAL: PARK this exact representation. No reserve rescue.
