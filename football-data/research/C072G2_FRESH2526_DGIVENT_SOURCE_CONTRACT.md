# C072-G2 — fresh 2025/26 conditional allocation source gate

## Lineage and isolation
- football3 only.
- P(T) information axis has C072-E2 development PASS and C072-F2 forward confirmation PASS.
- Parent checkpoint: `dd3f3e07690c0cd91685f47fb65421d364931654`.
- C073-C077 remain quarantined. Their hypotheses, metrics, labels, source choices and stopping rules are not inputs here.

## Why this source gate exists
Complete P(T) alone cannot produce exact scores. The next independent component is the allocation of an exact total between home and away, equivalently `P(H | T, X)` or `P(D | T, X)`.

Before choosing or scoring any D|T candidate, create a genuinely later fresh target pool whose score labels are not opened in this football3 line.

## Provider and fixed season
Provider: Football-Data.co.uk public CSV files.
Season: 2025/26 (`2526`).
URL pattern: `https://www.football-data.co.uk/mmz4281/2526/{division}.csv`.

Exact frozen divisions (12):
- England: E1, E2, E3
- Scotland: SC0, SC1, SC2, SC3
- Germany: D2
- Italy: I2
- Spain: SP2
- France: F2
- Portugal: P1

These are deliberately outside the eight-league 2009/10–2024/25 source used for C072-E2/F2, and provide a later time window.

## Stage
ZERO-LABEL / ZERO-RESULT source audit only.
No FTHG, FTAG, FTR, half-time result, exact total, goal difference, score class, or other target outcome may be materialized or numerically parsed.
No model may be fit.

## Allowed source fields
Only the following may be selected from each CSV:
- Div (if present)
- Date
- Time (if present)
- HomeTeam
- AwayTeam
- AvgH, AvgD, AvgA
- AvgCH, AvgCD, AvgCA

The source gate requires average 1X2 opening/pre-closing and closing prices because a later D|T model may need a market-strength axis, but this audit does not choose that model.

## Frozen gates
PASS only if all hold:
1. all 12 fixed URLs download and parse;
2. total identities >=4,000;
3. duplicate `(division, Date, HomeTeam, AwayTeam)` identities =0;
4. valid Date rate >=99.5%;
5. opening AvgH/AvgD/AvgA finite and >1 on >=95% of rows;
6. closing AvgCH/AvgCD/AvgCA finite and >1 on >=90% of rows;
7. among rows with both triplets, de-vig opening vs closing 1X2 distributions differ by L1 distance >1e-9 on >=80%;
8. every file has >=100 identities;
9. at least 10/12 files have closing-triplet coverage >=85%;
10. target/result values materialized=0;
11. model_fit=0.

Failure => `STOP_SOURCE_COVERAGE`; do not alter the same-source gates after seeing coverage.

## PIT classification
Football-Data documents opening/pre-closing and closing semantics, but this historical file does not provide immutable quote timestamps for each price. Therefore any later market feature from these files is research-grade coarse PIT only.

## If PASS
The 2025/26 score values remain sealed. A separate D|T development contract may use older already-consumed historical data to freeze a low-capacity allocation model. A later confirmation contract must be frozen before this C072-G2 target pool's score values are opened.

## Hard boundaries
- C072-F2 2024/25 labels are already consumed and cannot be called fresh D|T confirmation.
- C070-F Confirmation1597 remains sealed.
- protected samples remain sealed.
- C073-C077 quarantined.
- formal_weight=0; no CURRENT change; no exact-score matrix or Draw/1-1 boost at this stage.
