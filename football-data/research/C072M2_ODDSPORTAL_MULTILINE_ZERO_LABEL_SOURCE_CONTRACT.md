# C072-M2 — OddsPortal multi-line O/U zero-label source gate

## Lineage / reason
- football3 only, continuing from independent C072-L2 diagnosis.
- C072-L2 showed the remaining exact-score Top1 concentration is primarily P(T) resolution: the confirmed single-line O/U2.5 P(T) model calls T=2 Top1 on about 80% of K2 eligible matches.
- C073-C077 remain quarantined.

The next information target is therefore the **shape of the total-goals distribution**, preferably multiple O/U thresholds rather than tuning the same O/U2.5 representation.

## Source candidate
Open-source scraper: `jordantete/OddsHarvester`, pinned commit `5f6fc5e9768fcb089aa13c7d447aea8644a00b10` (v0.10.0).
Its published interface states football `over_under` is an umbrella market that expands to every O/U line rendered for a match.

Underlying public site: OddsPortal.
No account/API key/purchase is permitted for this gate.

## Stage
ZERO-LABEL / FUTURE-MATCH source audit only.
Use only currently upcoming, not-yet-finished football matches. No historical scores/results are requested or interpreted in this stage.

Fixed league slugs:
- england-premier-league
- spain-laliga
- germany-bundesliga
- italy-serie-a
- france-ligue-1

Command semantics are frozen to upcoming + `over_under` + preview-only + headless. Preview-only is sufficient because this gate asks whether multiple goal lines are publicly exposed, not which bookmaker is best.

## Allowed material
- match identity / URL / league
- kickoff metadata
- O/U market names/line values
- offered Over/Under prices
- scrape timestamp

Any non-null final score/result field => source gate hard failure for zero-label integrity.

## Frozen structural gates
`MULTILINE_SOURCE_PASS` only if ALL:
1. scraper process succeeds without account/API credentials;
2. at least 15 future football matches are returned pooled across the five fixed leagues;
3. at least 3 of the five leagues return >=2 matches;
4. every retained match has a future/unsettled status and no non-null final score/result;
5. at least two distinct half-goal O/U lines are observed on >=80% of matches;
6. at least three distinct half-goal O/U lines are observed on >=60% of matches;
7. O/U2.5 appears on >=90% of matches;
8. at least one adjacent lower or upper line (1.5 or 3.5) appears together with 2.5 on >=70% of matches;
9. no model fit and no target-result parsing.

If the scraper/site is blocked by anti-bot/network/geography, terminal is `SOURCE_ACCESS_BLOCKED`, not a scientific failure.
If access succeeds but structural coverage fails, terminal is `STOP_MULTILINE_SOURCE_COVERAGE`.
Do not relax these gates on the same audit after seeing results.

## If PASS
A separate C072-N2 historical-development contract must be frozen **before** any historical match result is parsed. N2 may test whether multi-line O/U shape improves P(T) proper scores and total-goal Top1 diversity beyond the confirmed single-line C072-F2 component.

A fresh confirmation pool must remain separate from N2 development.

## Hard boundaries
- no C/feature/transform/model changes in this source stage;
- K2/L2 consumed EC/T1/G1 labels cannot be used to tune this source;
- C070-F Confirmation1597 sealed;
- protected sealed;
- C073-C077 quarantined;
- T>=7 exact-score split unresolved;
- formal_weight=0; no CURRENT change; no Draw/0-0/1-1 boost.
