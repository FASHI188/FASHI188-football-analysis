# C072-J2 — fresh joint P(T) + P(H|T) zero-label source gate

## Lineage / isolation
- football3 only.
- C072-F2 independently confirmed the P(T) opening→closing O/U2.5 movement component.
- C072-I2 independently confirmed H2 Model A conditional allocation P(H|T,X), T=1..6.
- C073-C077 remain quarantined and provide no evidence, design input, thresholds or interpretation.

## Purpose
Before any low-score exact-score integration is evaluated, create a genuinely new target pool on which neither component has been scored in football3.

## Fixed fresh target pool
Provider: Football-Data.co.uk public CSV.
Season: 2025/26 (`2526`).
Exact fixed divisions:
- `EC` — England Conference / National League
- `T1` — Turkey top division
- `G1` — Greece top division

URL pattern: `https://www.football-data.co.uk/mmz4281/2526/{division}.csv`.

These divisions are outside the previous football3 C072-G2 12-division D|T target pool and outside the nm2890 eight-league P(T) development/confirmation source.

## Stage
ZERO-LABEL / ZERO-RESULT source audit only.
No FTHG/FTAG/FTR/HT result/total-goal/exact-score/goal-difference value may be materialized or numerically parsed.
No model may be fit.

## Allowed fields
Identity:
- Date
- Time if present
- HomeTeam
- AwayTeam

1X2 average market:
- AvgH, AvgD, AvgA
- AvgCH, AvgCD, AvgCA

O/U2.5 average market:
- Avg>2.5, Avg<2.5
- AvgC>2.5, AvgC<2.5

## Frozen gates
PASS only if ALL:
1. EC/T1/G1 all download and parse;
2. pooled raw identities >=800;
3. each division has >=200 identities;
4. duplicate `(division,Date,HomeTeam,AwayTeam)` =0;
5. valid Date rate >=99.5%;
6. opening 1X2 triplet finite and >1 on >=95% of rows;
7. closing 1X2 triplet finite and >1 on >=90% of rows;
8. opening+closing O/U2.5 four-price set finite and >1 on >=90% of rows;
9. among complete O/U2.5 rows, de-vig opening and closing Over probabilities differ by >1e-9 on >=80%;
10. every division has O/U2.5 four-price coverage >=85%;
11. target/result values materialized=0;
12. model_fit=0.

Failure => `STOP_JOINT_SOURCE_COVERAGE`. Do not alter gates on the same source after seeing coverage.

## PIT classification
Football-Data supplies opening/pre-closing and closing semantics but not immutable row-level quote timestamps. Therefore a later integration result is research-grade coarse PIT, not a formal timestamped market snapshot.

## If PASS
The 2025/26 EC/T1/G1 score values remain sealed. Freeze a separate C072-K2 joint-integration contract before opening them.

The K2 integration must mechanically combine only the already-frozen confirmed components; it may not refit or choose a new model on J2 targets.

## Hard boundaries
- C070-F Confirmation1597 remains sealed.
- protected samples sealed.
- C073-C077 quarantined.
- formal_weight=0; no CURRENT change.
- no target score read, no exact-score matrix or 0-0/1-1/Draw boost in this source stage.
