# C079-P1000R1 Public Page Expansion Contract

Status: zero-label source-engineering correction after C079-P1000 stopped at 455 complete rows because the Footiqo league index exposed only 10 league links. formal_weight=0.

The scientific/source-quality gates are unchanged. R1 changes only source discovery coverage and remains public-HTML-only.

## Frozen source set
Use the union of:
1. every public same-site league URL discovered from `https://footiqo.com/database/leagues/`; and
2. the following 15 already known public Footiqo league pages that are not reliably listed by that index:

- argentina-liga-profesional
- brazil-serie-a
- australia-a-league
- saudi-professional-league
- usa-mls
- portugal-liga-portugal
- scotland-premiership
- netherlands-eredivisie
- belgium-jupiler-pro-league
- greece-super-league
- turkey-super-lig
- denmark-superliga
- austria-bundesliga
- colombia-primera-a
- croatia-hnl

No league is selected using result labels. The supplemental list is frozen before R1 fetch.

## Data boundary
Identical to P1000:
- unauthenticated public GET only;
- no login, Premium/export endpoint, nonce/AJAX replay or bypass;
- stream-discard all bytes before `Historical Odds`;
- materialize only tables containing `id,matchDate,Country,League,Season,homeTeam,awayTeam,O25,U25,O35,U35,O45,U45`;
- never materialize FTHG/FTAG/FTR, target totals, tail membership or model outputs.

## Frozen PASS gates
- >=20 total frozen/discovered public league URLs;
- >=15 pages with required Odds table;
- >=1,000 unique identities with all six prices numeric and >1.0;
- nested de-vig coherence >=98%;
- zero conflicting duplicate identities;
- zero result/target/model access.

If enough rows exist, select exactly 1,000 by SHA256(identity_key) ascending, independent of odds values and outcomes.

Formal C079 >=3,000 gate remains unchanged. PASS only authorizes a separately preregistered 1,000-match development experiment.
