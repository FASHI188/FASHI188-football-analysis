# C078-C Frozen Contract — C076-D Fresh Pool O/U2.5 Market-Field Gate

Status: zero-label / zero-score-value source audit only. `formal_weight=0`.

## Purpose

After C078-A/B stopped distribution-family shopping on the viewed C074-E tail labels, C078-C changes the **data plan**, not the family. Before any new result label is opened, audit whether the still-sealed C076-D 2025/26 lower-league pool contains the same four Football-Data O/U2.5 opening/closing market-average fields used by C074-F/G and enough chronological coverage to support a future calibration→confirmation design.

## Frozen source

Provider: Football-Data.co.uk, season path `mmz4281/2526`.

Fixed codes inherited from the already-sealed C076-D identity pool:
`E1,E2,E3,SC0,SC1,SC2,SC3,D2,I2,SP2,F2,P1`.

C076-D sealed identity anchor:
- identity count = `4567`;
- identity SHA256 = `fea3360a19094337579f1348858c7298e0b1bce1a177174cafc8e31dfd12c710`;
- duplicate identities = `0`.

The C078-C audit must reproduce that exact identity count/SHA before interpreting market coverage.

## Allowed materialization

CSV reader may materialize only:
- `Div` if present;
- `Date`;
- `Time` if present;
- `HomeTeam`;
- `AwayTeam`;
- `Avg>2.5`;
- `Avg<2.5`;
- `AvgC>2.5`;
- `AvgC<2.5`.

Raw HTTP bytes may be downloaded/hashed as in C076-D, but `FTHG`, `FTAG`, `FTR`, half-time scores/results and every other outcome column must **not** be selected into the parser/dataframe.

The artifact must explicitly report target/result columns materialized = 0, score numeric conversions = 0, total-goal calculations = 0, tail membership calculations = 0, model fit = 0.

## Frozen market definition

For rows with all four values finite and >1:
- `p_open = (1/Avg>2.5) / [(1/Avg>2.5)+(1/Avg<2.5)]`;
- `p_close = (1/AvgC>2.5) / [(1/AvgC>2.5)+(1/AvgC<2.5)]`;
- `movement_logit = logit(p_close)-logit(p_open)`.

This uses market prices only and no result label.

## Frozen zero-label chronological split audit

Dates are parsed day-first. For future design feasibility only, report counts before and on/after `2026-01-01`:
- early = date < 2026-01-01;
- late = date >= 2026-01-01.

This split is frozen before any numeric score access. It is not yet authorization to use either side as calibration/confirmation.

## PASS gate

All must pass:
1. exactly 12 frozen files available;
2. C076-D identity count exactly 4567;
3. C076-D identity SHA exactly `fea3360a19094337579f1348858c7298e0b1bce1a177174cafc8e31dfd12c710`;
4. duplicate identity count = 0;
5. all four O/U2.5 columns exist in every file;
6. valid identity dates >=99.5%;
7. complete-valid four-market-price rows >=3500;
8. complete-valid four-price coverage >=75% of the 4567 frozen identities;
9. at least 8 of 12 files individually have >=70% complete-valid four-price coverage;
10. nonzero de-vig open→close movement rate among market-valid rows >=5%;
11. early market-valid rows >=1400;
12. late market-valid rows >=1400;
13. target/result label columns materialized = 0 and all score/goal/tail/model boundary flags remain false.

If PASS: `PASS_FRESH_MARKET_ZERO_LABEL_GATE`.
If FAIL: `STOP_SOURCE_MARKET_COVERAGE`; do not open scores and seek another fresh market source.

## Post-gate rule

A PASS only permits freezing the next C078-C calibration→confirmation scientific contract. It does **not** itself authorize opening C076-D score values.

Before score access, the next contract must separately freeze:
- the exact full-support baseline fitted only on already-viewed historical development data;
- the single calibration mechanism and all parameters allowed to be estimated on a frozen fresh calibration block;
- PIT feature-history/warmup rules;
- immutable calibration/confirmation identity sets and minimum realized-tail coverage gates;
- confirmation proper-score, absolute 8+/9+ calibration, stability and stopping rules;
- no use of the confirmation block to tune the calibration mechanism.

## Hard boundaries

C077-B 6943 labels remain quarantined; C076-D scores remain sealed; C071 reserve52180, C070-F1597, A05/protected remain unopened. CURRENT/main/formal_weight/unified matrix unchanged.
