# C074-J Frozen Contract — Extra-16 Calendar-2025 Exact-Tail Zero-Label Gate

Status: source/identity/date audit only. `formal_weight=0`. No score, result, total-goal or tail label may be materialized.

## Purpose
Open a second genuinely untouched external confirmation domain after C074-I correctly stopped on its preregistered `test_tail_rows>=80` coverage gate (70 observed). The C074-I threshold is not relaxed and those opened labels are not reused for scientific repair.

Football-Data.co.uk separately publishes 16 worldwide premier-division result files under `/new/*.csv`, historically distinct from the main 22-division season files used in C074-F/G/H/I.

## Frozen source files
- ARG Argentina
- AUT Austria
- BRA Brazil
- CHN China
- DNK Denmark
- FIN Finland
- IRL Ireland
- JPN Japan
- MEX Mexico
- NOR Norway
- POL Poland
- ROU Romania
- RUS Russia
- SWE Sweden
- SWZ Switzerland
- USA USA

URLs: `https://www.football-data.co.uk/new/{CODE}.csv`.

## Frozen confirmation window
The downstream confirmation test window is the completed natural calendar year `2025-01-01` through `2025-12-31`, regardless of domestic season convention. Policy window will be calendar 2024; train will be all valid history strictly before 2024.

This calendar split is fixed before any score/tail label is opened.

## Zero-label materialization rule
The audit may inspect the CSV header and materialize only identity/date columns. Accepted aliases are:
- date: `Date`
- home: `Home` or `HomeTeam`
- away: `Away` or `AwayTeam`
- optional non-target identifiers such as `Country`, `League`, `Season` if present.

Forbidden: `HG`, `AG`, `FTHG`, `FTAG`, `Res`, `FTR`, any score/result field, any total-goal/tail field.

## Frozen PASS gate
All must hold:
1. >=12 of the 16 frozen files parse with Date + home + away identity columns;
2. >=3,500 valid identity/date rows fall inside calendar 2025;
3. valid identity/date rate within the 2025 candidate rows >=99.5%;
4. duplicate `(source_code,Date,home,away)` rows in 2025 = 0;
5. all test rows fall inside 2025-01-01..2025-12-31;
6. target/result/tail label columns materialized = 0;
7. model fits = 0.

No country/file may be selected based on goals or tail frequency.

## Post-gate rule
If PASS, freeze C074-K before opening any score label. C074-K must reuse the exact V5.1 R1 tail family and the same coverage requirement `confirmation T>=7 rows >=80`; it must not lower the threshold that stopped C074-I.

If FAIL, do not open score labels in these files under this track.

Protected C071 reserve52180, C070-F Confirmation1597, A05 and protected samples remain untouched.
