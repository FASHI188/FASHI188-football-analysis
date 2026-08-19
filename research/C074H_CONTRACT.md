# C074-H Frozen Contract — Untouched 2025/26 Secondary-League Exact-Tail Zero-Label Gate

Status: source/identity/date audit only. `formal_weight=0`. No score, result, total-goal or tail label may be materialized in this gate.

## Purpose
Create a genuinely untouched external forward domain for exact `T>=7` tail confirmation. The C074-G forward Direct-T confirmation opened 2025/26 result labels only for seven top divisions (`E0,SP1,I1,D1,F1,N1,B1`). C074-H explicitly excludes those divisions.

The tail candidate family is not being invented here. The intended downstream confirmation reuses the pre-existing V5.1 R1 exact-tail contract frozen before this 2025/26 domain existed: `pooled_geometric` vs `pooled_hurdle_geometric`, selected only on a policy period, with infinite support and evaluation bins `7,8,9,10,11+`.

## Frozen untouched 2025/26 candidate divisions
- E1 England Championship
- E2 England League One
- E3 England League Two
- SC0 Scotland Premiership
- SC1 Scotland Championship
- SC2 Scotland League One
- SC3 Scotland League Two
- D2 Germany 2. Bundesliga
- I2 Italy Serie B
- SP2 Spain Segunda Division
- F2 France Ligue 2
- P1 Portugal Primeira Liga
- G1 Greece Super League
- T1 Turkey Super Lig

Top divisions consumed by C074-G are excluded by construction.

## Zero-label materialization rule
For each `https://www.football-data.co.uk/mmz4281/2526/{DIV}.csv`, only these columns may be materialized:
- `Div`
- `Date`
- `Time` if present
- `HomeTeam`
- `AwayTeam`

`FTHG`, `FTAG`, `FTR`, half-time results, score-derived totals and every tail label are forbidden.

## Frozen source gate
PASS requires all:
1. at least 10 of the 14 frozen divisions parse successfully;
2. at least 3,500 valid 2025/26 identity rows across parsed divisions;
3. valid Date/HomeTeam/AwayTeam rate >= 99.5%;
4. all valid dates fall within 2025-07-01 through 2026-06-30;
5. duplicate `(Div,Date,HomeTeam,AwayTeam)` identity rows = 0;
6. target/result/tail label columns materialized = 0;
7. model fits = 0.

No row or division may be included/excluded based on goals, total-goal labels or eventual tail frequency.

## Post-gate rule
If PASS, freeze C074-I before opening any score labels. C074-I must reuse the pre-existing V5.1 R1 tail-law family and fixed binning. Candidate selection must occur only on historical policy data strictly before 2025/26; 2025/26 exact-tail labels may be used once for confirmation reporting only. No new tail law, threshold, competition subset, bin boundary, beta prior, empirical-baseline prior mass or bootstrap seed may be chosen after labels are opened.

If FAIL, no 2025/26 score labels in these divisions are opened under this track.

C071 reserve52180, C070-F Confirmation1597, A05 and protected samples remain untouched.
