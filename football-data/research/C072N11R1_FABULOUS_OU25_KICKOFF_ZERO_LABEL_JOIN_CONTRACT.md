# C072-N11R1 — fabul0us dynamic O/U2.5 kickoff zero-label join

## Lineage / classification
- Project: football3 only.
- Parent: C072-N11 branch from C072-N10 PARK.
- Source audit authoritative run: `32255527938`; artifact `football3-c072n11-fabul0us-dynamic-ou25-zero-label`.
- Dynamic odds source: `fabul0us/football_odds_2023-24`, revision `211feb35f9dcd270bd7a1b27b39a8b1f45f239aa`, `match_odds.csv` SHA256 `c0e8854302159e1a8c529463f33280b728909c5e0ba95262515a7a144a43aa2a`.
- N11 source audit established 1,956,225 rows and 2,006 `(competition,home,away)` identities, with zero result labels and zero model fits. The file has dynamic O/U2.5 timestamps but no native kickoff field.
- N11R1 is ZERO-LABEL ENGINEERING / PIT JOIN ONLY. It must not read football scores/results or fit/score a model.
- C073-C077 scientific conclusions remain quarantined. C070-F Confirmation1597 remains sealed.

## Purpose
Establish exact scheduled kickoff timestamps for the top-five-league portion of the 2,006-match dynamic O/U2.5 asset, then prove that T-24h, T-6h and T-1h market freezes can be reconstructed strictly before kickoff.

## Fixed zero-label kickoff source
Pinned repository: `nm2890/football-data` revision `279978313f9c16a210fa80e8986fa22f0f866fba`.

Fixed files:
- PREMIER LEAGUE -> `data/england/premier-league.csv`
- LIGA -> `data/spain/laliga.csv`
- BUNDESLIGA -> `data/germany/bundesliga.csv`
- SERIE A -> `data/italy/serie-a.csv`
- LIGUE 1 -> `data/france/ligue-1.csv`

N11R1 may materialize ONLY `Date`, `Season`, `HomeTeam`, `AwayTeam` from these files. `FTHG`, `FTAG`, `FTR`, half-time score/result fields and every derived outcome are forbidden.

Only source rows with `Season == 2023-2024` are eligible.

Champions League and Europa League identities are excluded from N11R1 because this pinned kickoff source does not cover them. They remain zero-label/unscored.

## Frozen dynamic identity aggregation
For each top-five `(competition,home_team,away_team)` identity in the immutable fabul0us CSV:
- retain every row with finite two-sided `odds_under_2.5 > 1` and `odds_over_2.5 > 1`;
- parse `U/O 2.5 timestamp` exactly as source time;
- repeated identical timestamps are deduplicated by timestamp only if their O/U2.5 price pair is identical; conflicting prices at the same identity/timestamp invalidate that dynamic identity;
- record min/max valid O/U2.5 timestamp.

## Frozen kickoff identity join
Canonical team string for both sources:
1. Unicode NFKD;
2. ASCII transliteration;
3. lowercase;
4. remove all non `[a-z0-9]` characters.

No manual alias dictionary is allowed in N11R1.

For each dynamic identity, candidate kickoff-source rows are restricted to:
- same fixed competition/source file;
- Season `2023-2024`;
- kickoff strictly after the dynamic identity's maximum O/U2.5 timestamp;
- kickoff no more than 7 days after that maximum timestamp.

Stage 1 exact canonical pair:
- accept if exactly one candidate has exact canonical home and exact canonical away.

Stage 2 conservative oriented fuzzy pair, only if Stage 1 has no match:
- Python `difflib.SequenceMatcher` separately on home and away canonical strings;
- `mean=(home_ratio+away_ratio)/2`;
- `min_side=min(home_ratio,away_ratio)`;
- sort by mean, then min_side, then kickoff;
- accept only if `min_side >= 0.30`, `mean >= 0.65`, and best mean minus second-best mean >= `0.15`;
- home/away swapping is forbidden.

If one kickoff-source identity is provisionally claimed by >1 dynamic identity, invalidate all members of that collision. No adaptive repair inside N11R1.

## Frozen PIT market freezes
For each accepted match and cutoff C in {24h,6h,1h}:
- cutoff timestamp = kickoff - C;
- choose the O/U2.5 observation with the greatest source `U/O 2.5 timestamp` satisfying observation timestamp <= cutoff timestamp;
- never use a later observation to backfill an earlier cutoff;
- the chosen observation must be strictly before kickoff by construction.

Persist the chosen O/U2.5 timestamp, Under price and Over price for each cutoff. Do not derive/read target outcomes.

## Frozen PASS gate
Terminal `C072N11R1_FABULOUS_OU25_KICKOFF_JOIN_PASS` requires ALL:
1. immutable fabul0us SHA exact;
2. all five kickoff identity sources materialize identity columns only;
3. target/result values materialized = 0;
4. unique accepted top-five kickoff joins >= 1,500;
5. accepted-join rate among top-five dynamic identities >= 85%;
6. one-to-one label-source assignment after collision adjudication;
7. accepted matches with complete T-24h/T-6h/T-1h two-sided O/U2.5 freezes >= 1,200;
8. complete-freeze rate among accepted matches >= 75%;
9. every selected freeze timestamp <= its cutoff and < kickoff;
10. conflicting same-timestamp O/U price identities = 0;
11. C070-F Confirmation1597 opened = false; model_fit=0; model_score=0.

If any gate fails: `C072N11R1_FABULOUS_OU25_KICKOFF_JOIN_STOP`. Do not relax identity thresholds/windows/PIT cutoffs on the same data and relabel it PASS.

## Downstream authorization if PASS
PASS authorizes exactly one next step: freeze a separate N12 scientific development contract before any score/result target for these 2023/24 identities is decoded.

Because the top-five 2023/24 result domain is already globally consumed elsewhere in the research program, any later N12 score is `REPLICATION / REPRODUCTION` only. It cannot be called fresh, blind, pristine or independent confirmation.

N12 should test whether the multi-time O/U2.5 trajectory improves complete match-level P(T), with proper score primary and no Draw/T=2/manual score boost.
