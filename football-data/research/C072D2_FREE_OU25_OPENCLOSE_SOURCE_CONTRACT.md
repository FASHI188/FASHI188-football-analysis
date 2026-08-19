# C072-D2 — no-login O/U2.5 opening/closing zero-label source gate

## Isolation
- Project: football3 only.
- Parent checkpoint: C072-C @ `e3e73c998020beef585cc459a69ea5b73b44ddb3`.
- C073-C077 are quarantined and cannot be used as evidence, design input, stopping-rule input, or interpretation.
- This is a weaker proxy branch parallel to C072-D Betfair BASIC. It does **not** satisfy the timestamped multi-line hypothesis.

## Source
Public repository `nm2890/football-data` pinned to revision `279978313f9c16a210fa80e8986fa22f0f866fba`.
Its README describes eight leagues (2009/10 through 2024/25) and average opening/closing prices for O/U2.5 from multiple bookmakers.

## Stage
SOURCE/COVERAGE AUDIT ONLY. No target results/goals may be materialized, no model may be fit, and no sealed project sample may be opened.

## Allowed columns
Only: `Date`, `Season`, `HomeTeam`, `AwayTeam`, `over_2.5_open`, `over_2.5_close`, `under_2.5_open`, `under_2.5_close`, plus source path as league identity.
Explicitly forbidden at this stage: `FTHG`, `FTAG`, half-time scores, result labels, total-goal labels, exact-score labels.

## Frozen source gates
PASS only if all hold:
- exact expected CSV files present = 8
- total parsed match identities >= 30,000
- duplicate `(source_file, Date, HomeTeam, AwayTeam)` identities = 0
- valid dates >= 99.5%
- all four O/U2.5 prices finite and >1 for >=80% of rows
- among complete-price rows, de-vig opening and closing Over probabilities differ by >1e-9 for >=5% of rows
- at least 6/8 files each have >=70% complete four-price coverage
- target/result columns materialized = 0
- model_fit = 0

Failure => `STOP_SOURCE_COVERAGE`. Gates may not be relaxed on the same source revision.

## PIT limitation
The source gives opening/closing semantics but no immutable original quote timestamp for each row. Therefore a PASS only authorizes a **research-grade coarse-PIT proxy**. It cannot be called dynamic multi-line O/U, a synchronized market snapshot, or formal PIT evidence.

## If PASS
A separate C072-E2 scientific contract must be frozen before target labels are read. Any candidate must be low-capacity and compare against the frozen score-history P(T) baseline; no feature/C/window/league-subset search after labels.

## Hard boundaries
- C072-C 959 viewed labels may not be reused for tuning.
- C073-C077 quarantined.
- C070-F Confirmation1597 remains sealed.
- protected samples remain sealed.
- formal_weight=0; no CURRENT change; no exact-score matrix or Draw/1-1 boost.
