# C078-E3 Frozen Contract — Supplemental Calibration Source Gate

Status: zero-label / zero-score-value source gate only; `formal_weight=0`.

## Purpose
C078-E2 stopped before fitting gamma because its frozen early block had only 26 realized T>=7 rows versus the preregistered minimum 35. No candidate scientific metrics were computed and the late 2,119 block remains sealed. C078-E3 seeks additional calibration-only identities without touching that late confirmation block.

## Fixed new source codes
Football-Data 2025/26: `EC`, `T1`, `G1`.
These codes are disjoint from:
- C074-G viewed codes: `E0,SP1,I1,D1,F1,N1,B1`;
- C078-D frozen codes: `E1,E2,E3,SC0,SC1,SC2,SC3,D2,I2,SP2,F2,P1`.

## Allowed reads
- file bytes/hash and CSV headers;
- Date, HomeTeam, AwayTeam, optional Div/Time;
- four O/U2.5 market columns: `Avg>2.5`,`Avg<2.5`,`AvgC>2.5`,`AvgC<2.5`;
- boolean non-empty presence only of FTHG and FTAG to exclude unfinished fixtures.

## Forbidden reads
No numeric conversion/storage/hash/comparison of FTHG/FTAG; no FTR/HT scores; no T/D/tail membership; no model fit or scientific metric.

## Frozen source gate
PASS requires all:
1. all 3 fixed files available;
2. required identity, market and FTHG/FTAG-presence columns exist in every file;
3. at least 700 completed identities with all four market prices >1.0;
4. duplicate identity count =0;
5. valid dates >=99.5%;
6. market-valid/completed fraction >=80% of parsed identities;
7. at least 2/3 files individually have market-valid/completed coverage >=75%;
8. nonzero de-vig open→close movement rate >=5%;
9. all frozen identities lie in 2025-07-01 through 2026-06-30;
10. numeric target/result materialization count=0.

If PASS, freeze a market-only snapshot containing only identity + four market prices, raw file hashes and identity SHA. Raw score-bearing bytes remain transient and are not persisted.

If FAIL, terminal `STOP_SUPPLEMENTAL_SOURCE`; do not inspect score values and seek a different new source.

## Downstream if PASS
A separate expanded-calibration contract must be frozen before any EC/T1/G1 score values are numerically accessed. That contract may combine the already-consumed C078-E2 early calibration labels with this new supplemental calibration-only pool exactly once. The existing C078-D late 2,119 block remains fully sealed and cannot be used to satisfy calibration sample size.

CURRENT/main/formal weights/unified matrix remain unchanged.