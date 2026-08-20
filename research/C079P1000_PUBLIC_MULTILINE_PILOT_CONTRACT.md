# C079-P1000 Public Multi-Line O/U Pilot Contract

Status: preregistered source-feasibility pilot only. formal_weight=0.

User authorization: run a 1,000-match pilot before the unchanged formal C079 >=3,000 source gate.

## Purpose
Test whether public Footiqo league HTML can lawfully supply a sufficiently large same-match O/U 2.5/3.5/4.5 market-only pack. This stage does not test prediction quality and does not change CURRENT V5.2.

## Source rule
- Entry point: https://footiqo.com/database/leagues/
- Discover only publicly indexed same-site `/database/leagues/<slug>/` links.
- GET public HTML only. No login, Premium/export endpoint, AJAX bypass, nonce replay or authenticated request.
- For every league page, stream-discard everything before the literal `Historical Odds` marker.
- Materialize only tables containing all required market columns: `id, matchDate, Country, League, Season, homeTeam, awayTeam, O25, U25, O35, U35, O45, U45`.
- FTHG/FTAG/FTR, total goals, tail membership and any result/score target are forbidden.

## Frozen pilot gates
PASS_PILOT1000 requires all:
1. index HTTP 200 and >=20 league URLs discovered;
2. >=15 league pages expose at least one exact required Odds table;
3. >=1,000 unique identities with all six O/U prices numeric and >1.0;
4. de-vig nesting `P(O2.5)>=P(O3.5)>=P(O4.5)` on >=98% of complete rows;
5. no conflicting duplicate identity/market rows;
6. result/score fields materialized = 0; target/model computations = 0.

If >=1,000 eligible rows exist, the immutable pilot pack is the first exactly 1,000 rows by SHA256(identity_key) ascending after complete-price filtering. Selection never uses result labels or price values.

## Governance
- The formal C079 >=3,000 gate is unchanged.
- A pilot PASS authorizes only a separately preregistered 1,000-match development experiment; it is not promotion, confirmation, or formal exact-tail closure.
- C078-D late 2,119, C076-D, C071 reserve 52,180, C070-F 1,597, A05/protected and all other sealed pools remain unopened.
- Exact-tail remains BLOCKED and unified exact-score matrix remains unavailable at this stage.
