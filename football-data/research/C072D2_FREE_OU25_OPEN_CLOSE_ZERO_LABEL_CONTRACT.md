# C072-D2 — Free O/U2.5 Open/Close Zero-Label Source Contract

Project: football3
Parent scientific state: C072-C only (`e3e73c998020beef585cc459a69ea5b73b44ddb3`).
Quarantine boundary: C073–C077 are not ancestry/evidence/tuning/stopping-rule input for this experiment.
Formal weight: 0.

## Question
Can a completely public, no-login source provide broad historical pre-match O/U2.5 opening and closing odds coverage sufficient for a coarse market-movement research axis after C072-C?

This is a source/coverage audit only. It is NOT dynamic multi-line O/U, because the source exposes only average opening/closing odds for the 2.5 line and does not preserve immutable quote timestamps.

## Pinned external source
- Repository: `nm2890/football-data`
- Revision: `279978313f9c16a210fa80e8986fa22f0f866fba`
- Fixed league CSVs:
  - data/england/premier-league.csv
  - data/spain/laliga.csv
  - data/italy/serie-a.csv
  - data/germany/bundesliga.csv
  - data/france/ligue-1.csv
  - data/belgium/jupiler-pro-league.csv
  - data/netherlands/eredivisie.csv
  - data/egypt/premier-league.csv

## Allowed columns only
The audit may materialize only:
- Date
- country
- league
- Season
- HomeTeam
- AwayTeam
- over_2.5_open
- under_2.5_open
- over_2.5_close
- under_2.5_close

Forbidden columns include FTHG, FTAG, HTHG, HTAG and any derived score/result/goal-total label. The audit must use `pandas.read_csv(..., usecols=ALLOWED_COLUMNS)` so forbidden outcome columns are never materialized.

## Deterministic audit calculations
For rows with all four O/U prices finite and >1:
- de-vig opening over probability = `(1/O_open) / ((1/O_open)+(1/U_open))`
- de-vig closing over probability = `(1/O_close) / ((1/O_close)+(1/U_close))`
- movement = closing_over_prob - opening_over_prob
- movement_logit = logit(closing_over_prob) - logit(opening_over_prob)

No target outcome is used anywhere.

## Frozen source PASS gates
All must pass:
1. all 8 fixed CSVs download at the pinned revision;
2. required allowed columns present in every file;
3. total identity rows >= 30,000;
4. valid Date >= 99.5%;
5. complete-valid four-price rows >= 80% of identities;
6. at least 8 leagues and at least 12 seasons represented;
7. duplicate identity `(Date,country,league,HomeTeam,AwayTeam)` rate <= 0.1%;
8. among complete-valid rows, nonzero de-vig opening→closing movement fraction >= 5%;
9. target/result/score columns materialized = 0;
10. model_fit = 0 and model_score = 0.

## Interpretation boundary
A PASS means only `COARSE_OU25_OPEN_CLOSE_SOURCE_PASS`.
It permits a separately preregistered C072-D3 development experiment that tests whether the fixed opening→closing O/U2.5 movement scalar adds proper-score information for P(T).

A PASS must NOT be described as:
- timestamped multi-line O/U;
- synchronized market snapshot;
- Betfair-equivalent data;
- formal PIT market evidence;
- model confirmation or promotion.

C072-D Betfair BASIC remains the preferred full-structure route if account-side data become available.

## Protected boundaries
Do not open:
- C070-F Confirmation 1597;
- any C071 sealed confirmation labels;
- A05/protected assets;
- any C073–C077 result/label artifacts for model choice.

No formal model/CURRENT change is authorized by this contract.
