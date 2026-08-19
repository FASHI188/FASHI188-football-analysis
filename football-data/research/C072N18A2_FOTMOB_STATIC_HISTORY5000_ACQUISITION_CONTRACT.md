# C072-N18A2 — FotMob static shot-xG history5000 acquisition contract

## Status
- project: `football3`
- parent discovery HEAD: `e96b14e8a29d9fa68167b8ace1e4740b5b5d7b17`
- branch: `football3/c072n18a2-fotmob-static-history5000-20260819`
- operation: **one-shot historical feature-source acquisition**
- target retained matches: exactly **5,000**
- no model fit, no target cohort, no target score.

## Frozen upstream release
Repository: `JaseZiv/worldfootballR_data`
Release id: `79989708`
Release tag: `fotmob_match_details`
Published: `2022-10-15T14:20:26Z`

Metadata-only N18A1 discovery run `32275378990` established 9 CSV assets and downloaded zero match-data bytes.

## Frozen asset set
Use exactly these six domestic-league CSV assets, chosen from league identity only before content access:
- `47_match_details.csv` — England Premier League
- `87_match_details.csv` — Spain LaLiga
- `54_match_details.csv` — Germany 1. Bundesliga
- `55_match_details.csv` — Italy Serie A
- `53_match_details.csv` — France Ligue 1
- `130_match_details.csv` — USA MLS

Explicitly exclude international/cup assets `42`, `50`, `73` and all RDS copies.

Why MLS is included: it is a domestic-league asset from the same frozen release and provides non-overlapping coverage capacity before any content/result access. This choice is frozen before downloading match-detail data and is not outcome-driven.

## Frozen schema aliases
The acquisition may accept only these predeclared aliases; no schema shopping after data inspection:
- match identity: `match_id`
- competition: `league_id`, `league_name`
- season: `parent_league_season`
- date/time: `match_time_utc`
- side identity: `team_id`, `home_team_id`, `away_team_id`
- team labels: `home_team_name`, `away_team_name`
- xG: first existing of `expected_goals`, `expectedGoals`
- coordinates: `x`, `y`
- minute: first existing of `min`, `minute`
- added time: first existing of `min_added`, `minAdded`
- shot situation: `situation`
- body/shot type: first existing of `shot_type`, `shotType`

If required identity/side/xG columns are absent, STOP_SCHEMA. Do not add new aliases post-view on the same run.

## Usable-match gate
A match is usable when:
1. `match_id`, both team IDs and match time are valid;
2. at least 6 rows have finite numeric xG in [0, 1.5];
3. at least 1 finite xG shot belongs to the home side;
4. at least 1 finite xG shot belongs to the away side.

No score/result/goal flag is part of usability.

## Deterministic 5,000 selection
1. download all six frozen CSV assets;
2. parse only the predeclared identity/chance-state columns into the retained analytical tables;
3. build the usable-match inventory;
4. rank each usable match by `SHA256("C072N18A2_FOTMOB_HISTORY5000|<match_id>")`;
5. tie-break integer `match_id` ascending;
6. retain the first exactly 5,000 usable matches;
7. if usable inventory <5,000: terminal `STOP_COVERAGE`; do not lower the xG gate or add assets.

## Persisted outputs
Only the following processed artifacts may be retained:
1. `fotmob_n18a2_history5000_matches.jsonl.gz`
   - match_id/date/league/season/home-away identity
   - numeric-xG shot counts by side
   - xG sum/mean/variance by side
   - high-quality chance counts (`xG>=0.20`) by side
2. `fotmob_n18a2_history5000_shots.jsonl.gz`
   - match_id/is_home/xG/x/y/minute/added_time/situation/shot_type
3. `fotmob_n18a2_all_source_match_ids.txt.gz`
   - all distinct match IDs encountered in the six frozen assets, for global-consumption overlap governance
4. `fotmob_n18a2_history5000_summary.json`
   - source asset IDs/names/declared sizes/download hashes
   - selected/usable/source match counts
   - date and competition coverage
   - processed artifact SHA-256 digests.

Raw upstream CSV assets must not be uploaded as football3 artifacts after processing.

## Outcome-field boundary
Do not persist or use:
- home/away final score
- winner/result labels
- goal flag / event outcome
- shot outcome/on-target status
- standings
- any derived T/HDA/Draw target.

Because the upstream CSV files are full retrospective match-detail assets, **all distinct match identities decoded from these six files are conservatively classified GLOBALLY CONSUMED as future target identities**. They may only serve as historical feature-source rows. The later N18 target cohort must be separate and outside this source identity set.

## Scientific boundary
This acquisition does not authorize:
- target labels or market join;
- selection of latent family/half-life/regularization;
- B0/C fitting;
- LogLoss/Brier/RPS scoring;
- C070-F Confirmation1597;
- sealed football3 reserves;
- C073-C077 scientific conclusions.

## PASS
`PASS_FOTMOB_STATIC_HISTORY5000` requires:
- exactly 5,000 selected usable matches;
- all six asset downloads resolved from release id 79989708;
- selected matches have complete side identity and xG gate;
- persisted outcome target fields = 0;
- model fits = 0;
- target scores = 0.

Only after PASS may N18-B freeze the separate target cohort, market anchor, historical-state equations and development/confirmation split.
