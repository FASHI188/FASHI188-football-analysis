# C078-D Frozen Contract — Immutable Fresh 4,184 Market Snapshot

Status: zero-label / zero-result-value source freeze only. `formal_weight=0`.

## Purpose
C078-C established that the live Football-Data 2025/26 lower-league source no longer reproduces the old C076-D 4,567-identity seal, while its O/U2.5 opening/closing market coverage is excellent. The old raw snapshot is not recoverable from project assets. C078-D therefore freezes the currently observed live source as a **brand-new fresh pool** rather than pretending it is C076-D.

## Fixed source
Football-Data 2025/26 files: `E1,E2,E3,SC0,SC1,SC2,SC3,D2,I2,SP2,F2,P1`.
Expected zero-label identity receipt from C078-C:
- identity count = `4184`
- identity SHA256 = `7762c0f94adf3e734d7fce7f73dd203b61a761fafb733f717b939f3db35423ce`
- split date = `2026-01-01`
- expected market-valid early/late counts = `2065 / 2119`

## Allowed materialization
Only `Div`/`Date`/`Time`/`HomeTeam`/`AwayTeam` plus `Avg>2.5`,`Avg<2.5`,`AvgC>2.5`,`AvgC<2.5` may be materialized. A durable market-only snapshot may be emitted and committed because it contains no score/result label.

## Forbidden materialization
`FTHG`, `FTAG`, `FTR`, half-time score/result fields, any goal total, goal difference, tail membership, exact score, or model fit.

Raw source bytes may exist only transiently in the runner for header/hash verification. They MUST NOT be uploaded as an artifact or committed. The durable artifact may contain only market/identity-only files, raw file hashes/byte counts, contract, and summary.

## Hard PASS gate
All must hold on the single frozen run:
1. all 12 files available;
2. identity count exactly 4,184;
3. identity SHA exactly `7762c0f94adf3e734d7fce7f73dd203b61a761fafb733f717b939f3db35423ce`;
4. duplicate identities = 0;
5. all four O/U2.5 market columns exist in all files;
6. valid identity dates >= 99.5%;
7. market-valid rows exactly 4,184 and fraction = 100%;
8. nonzero de-vig movement rate >= 5%;
9. early/late market-valid counts exactly 2,065 / 2,119 under split `2026-01-01`;
10. target/result columns materialized = 0;
11. durable `market_snapshot.csv` identity SHA equals the source identity SHA.

Any mismatch is `STOP_SOURCE_DRIFT` and does not authorize label access.

## Downstream boundary if PASS
Freeze the exact market-only snapshot and its digest. Then preregister C078-E calibration→confirmation **before** any numeric score access. The intended split is early block `<2026-01-01` calibration and late block `>=2026-01-01` sealed confirmation. Calibration may not inspect late score labels. Confirmation is one-shot after all calibration transforms and gates are frozen.

No C077-B labels, C071 reserve52,180, C070-F1,597, A05/protected, formal weights, CURRENT, unified matrix, or exact-score outputs are touched.