# C074-F Frozen Contract — Football-Data.co.uk 2025/26 Zero-Label Confirmation Source Gate

Status: source/PIT/coverage audit only. `formal_weight=0`. Target/result labels must not be materialized, scored, summarized, or inspected in this gate.

## Purpose
Audit whether the public, no-login Football-Data.co.uk 2025/26 European league files provide a genuinely later, usable research confirmation domain for the exact frozen C074-E Direct-T method.

The C074-E development source (`nm2890/football-data`, pinned `279978313f9c16a210fa80e8986fa22f0f866fba`) ends at 2024/25. Therefore 2025/26 is temporally outside the C074-D/E development target period.

## Candidate confirmation source
Base URL: `https://www.football-data.co.uk/mmz4281/2526/`
Frozen divisions:
- `E0` England Premier League
- `SP1` Spain La Liga
- `I1` Italy Serie A
- `D1` Germany Bundesliga
- `F1` France Ligue 1
- `N1` Netherlands Eredivisie
- `B1` Belgium First Division A

Expected market columns, based on Football-Data's post-2019/20 open/closing convention:
- pre-closing/open-stage market average: `Avg>2.5`, `Avg<2.5`
- closing market average: `AvgC>2.5`, `AvgC<2.5`

Identity columns allowed in this gate: `Div`, `Date`, `Time` if present, `HomeTeam`, `AwayTeam`.

## Hard zero-label rule
The audit reader MUST call `pandas.read_csv(..., usecols=ALLOWED_ZERO_LABEL_COLUMNS)` or equivalent. It must never materialize `FTHG`, `FTAG`, `FTR`, half-time results, score-derived columns, or any settlement/result label.

The produced artifact must explicitly report `target_label_columns_materialized = 0` and `model_fit = 0`.

## Frozen coverage/PIT gate
PASS requires all:
1. >= 6 of the 7 frozen leagues downloaded and parsed;
2. >= 2,000 rows with valid Date/HomeTeam/AwayTeam and all four O/U2.5 average price columns > 1.0;
3. complete-valid four-price coverage >= 85% among parsed identity rows;
4. valid dates >= 99.5%;
5. nonzero de-vig open-stage→closing movement rate >= 5%;
6. all valid rows are in the 2025/26 season window (2025-07-01 through 2026-06-30, allowing league-specific season start/end inside that range);
7. target/result label columns materialized = 0;
8. model fits = 0.

## PIT boundary
Football-Data documents two sets of odds since 2019/20: an earlier set collected after market opening at scheduled collection times and a second closing set indicated by `C` in the headers. This is materially stronger temporal semantics than the C074-C/D mirror, but this gate does not assume immutable exchange quote timestamps. It remains aggregated bookmaker/market data.

## Source independence semantics
C074-F is a temporal independence gate, not a claim that the underlying bookmakers are economically independent from those represented in C074-D/E. The intended confirmation independence is that 2025/26 match outcomes occur strictly after the 2009/10–2024/25 development source span.

## Post-gate rule
If PASS, freeze C074-G before opening any 2025/26 result labels. C074-G must use the exact C074-E information architecture, feature transform, C=.1 multinomial-logistic family, >=8 prior-result history rule, same-date predict-before-update semantics, movement definition, proper-score metrics, and preregistered acceptance logic. No parameter/feature/fold/league-subset search is authorized.

If FAIL, do not open 2025/26 result labels through this source; seek another zero-label source.

Protected assets C071 reserve52180, C070-F Confirmation1597, A05, and protected samples remain untouched.
