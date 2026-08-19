# C072-N2 — Footiqo historical multi-line O/U zero-label source gate

## Lineage / reason
- football3 only, directly continuing from independent C072-M2 (`SOURCE_ACCESS_BLOCKED`).
- C072-L2 diagnosed the remaining exact-score Top1 bottleneck as match-level `P(T)` resolution: the confirmed single-line O/U2.5 model calls T=2 Top1 on about 80% of K2 eligible matches.
- C072-M2 attempted OddsPortal multi-line O/U but access/rendering was blocked; this was not a scientific source failure.
- C073-C077 remain quarantined and are not evidence/design input.

## Frozen scientific-source question
Can a no-login public historical source expose a sufficiently complete **cross-sectional total-goal curve** at multiple half-goal thresholds (0.5/1.5/2.5/3.5/4.5) to justify a separately preregistered P(T) development experiment?

This source gate does not fit or score any football model.

## Fixed source pages
Public Footiqo league database pages:
- `https://footiqo.com/database/leagues/england-premier-league/`
- `https://footiqo.com/database/leagues/spain-laliga/`
- `https://footiqo.com/database/leagues/germany-bundesliga/`
- `https://footiqo.com/database/leagues/italy-serie-a/`
- `https://footiqo.com/database/leagues/france-ligue-1/`

The public page states the historical odds view contains 1X2 plus O/U 0.5, 1.5, 2.5, 3.5 and 4.5 goal closing odds sourced from 1xBet and supports current/last-seasons views.

## Zero-label parsing rule
The HTTP response may co-locate other page sections, but the evaluator must **not structurally parse them**.

Before any HTML parser is invoked, raw HTML is sliced starting at the literal `Historical Odds: 1X2, Over/Under Goals, BTTS` heading. Only tables inside that sliced odds section whose headers contain the fixed odds schema may be parsed.

Allowed materialized columns only:
- `id`, `matchDate`, `Country`, `League`, `Season`, `homeTeam`, `awayTeam`
- `H`, `D`, `A`
- `O05`, `U05`, `O15`, `U15`, `O25`, `U25`, `O35`, `U35`, `O45`, `U45`
- `BTTSY`, `BTTSN`

Forbidden target/result columns include `FTHG`, `FTAG`, `FTR`, period scores, exact total goals and derived outcome labels. No target/result field may be selected from another page section.

## Fixed audit calculations
For each available O/U line L in {0.5,1.5,2.5,3.5,4.5} with valid decimal Over/Under prices >1:
- de-vig Over probability = `(1/O_L)/((1/O_L)+(1/U_L))`.

For rows with all five valid lines, check structural CDF monotonicity:
`P(Over0.5) >= P(Over1.5) >= P(Over2.5) >= P(Over3.5) >= P(Over4.5)`.

No result label is required for any source-gate calculation.

## Frozen source gates
Terminal `C072N2_MULTILINE_SOURCE_PASS` only if ALL:
1. all five fixed pages return HTTP-successful HTML with the historical-odds heading;
2. at least one matching odds table is parsed from every fixed league;
3. >=4,000 unique odds identities pooled;
4. >=5 distinct seasons pooled;
5. >=4 of five leagues have >=500 retained identities;
6. valid O/U2.5 two-sided price coverage >=90% pooled;
7. simultaneous valid 1.5+2.5+3.5 two-sided coverage >=80% pooled;
8. simultaneous valid all-five-line two-sided coverage >=60% pooled;
9. among all-five-line rows, de-vig Over probabilities are monotone in threshold on >=95% of rows;
10. duplicate `(id, League, Season)` identity rate <=0.5%;
11. target/result values materialized = 0;
12. model_fit = 0 and model_score = 0.

If HTTP/anti-bot/cloud access prevents the historical odds section from being retrieved, classify `SOURCE_ACCESS_BLOCKED`, not scientific failure.
If access succeeds but structural coverage fails, classify `STOP_MULTILINE_SOURCE_COVERAGE`.
Do not relax these thresholds after viewing the audit.

## If PASS
Freeze a separate C072-O2 development contract **before any football result label from this source is parsed**.

O2 should test whether the frozen multi-line market shape improves complete `P(T=0..7+)` proper scores and, specifically, reduces the pathological T=2 Top1 concentration relative to the already-confirmed single-line C072-F2 P(T) component. Model/representation choice and a truly later/fresh confirmation domain must be frozen separately.

## Hard boundaries
- Footiqo odds are treated as a historical closing snapshot; immutable original quote timestamps are not established, so this remains research-grade/coarse PIT evidence.
- No Draw/0-0/1-1 manual boost.
- No K2/L2 consumed target labels used for feature/model selection.
- C070-F Confirmation1597 sealed.
- protected samples sealed.
- C073-C077 quarantined.
- T>=7 exact-score split remains unresolved.
- formal_weight=0; no CURRENT/formal promotion.
