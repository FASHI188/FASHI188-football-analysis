# C072-N18B — Zero-label target cohort + market-anchor + historical chance-state join

## Status
- project: `football3`
- parent: C072-N18A2 postrun HEAD `0327b9eee154206cd74b9fadf146445969c846fb`
- branch: `football3/c072n18b-zero-label-target-market-join-20260820`
- phase: **ZERO TARGET LABELS ONLY**
- no model fit, no target score, no scientific-effect claim.

## Scientific purpose
Prepare the first executable N18 cohort without opening any outcome. N18B must join:
1. a **separate target identity pool** outside all 9,310 globally-consumed FotMob source identities;
2. a fixed prematch total-goal market anchor;
3. strictly historical shot/xG chance-state features built only from already-consumed FotMob history before the target fixture;
4. a chronological development/confirmation split frozen before any target outcome access.

## Historical chance-state source
Use the same six static FotMob release assets already consumed in N18A2:
`47/87/54/55/53/130_match_details.csv` from release id `79989708`, tag `fotmob_match_details`.

All 9,310 source match identities are already globally consumed as target identities and are allowed here only as historical feature-source rows.

For N18B feature reconstruction, use **all usable historical matches** under the already frozen N18A2 usability rule, not only the hash-selected 5,000 subset. This is permitted because:
- no new target evidence is created;
- all source identities were already classified globally consumed;
- complete historical sequences avoid missing-match bias introduced by a random 5,000-row source subset.

No event outcome / goal flag / final score may enter the feature equations.

## Target market source
Footiqo public `Historical Odds: 1X2, Over/Under Goals, BTTS` **odds-only** server-side table.

Fixed league pages:
1. `https://footiqo.com/database/leagues/england-premier-league/`
2. `https://footiqo.com/database/leagues/spain-laliga/`
3. `https://footiqo.com/database/leagues/germany-bundesliga/`
4. `https://footiqo.com/database/leagues/italy-serie-a/`
5. `https://footiqo.com/database/leagues/france-ligue-1/`
6. `https://footiqo.com/database/leagues/usa-mls/`

Exact odds schema remains the established 22-column Footiqo schema:
`id,matchDate,Country,League,Season,homeTeam,awayTeam,H,D,A,O05,U05,O15,U15,O25,U25,O35,U35,O45,U45,BTTSY,BTTSN`.

Forbidden in N18B requests/materialization:
`FTHG,FTAG,FTR`, any score/result, match statistics, xG result tab, standings or post-match target.

## Frozen target time window
- earliest target local match date/time: **2024-09-18 00:00**
- latest target local match date/time: **2024-12-31 23:59**

Expected season filter:
- England/Spain/Germany/Italy/France: `2024/2025`
- MLS: `2024`

This window was selected before odds-table content access because N18A2 historical source ends at 2024-09-16T16:30Z; the one-day buffer enforces temporal separation.

## Identity normalization
Zero-label cross-provider identity normalizer is fixed before target acquisition:
1. Unicode NFKD -> ASCII where possible;
2. lowercase;
3. `&` -> `and`;
4. punctuation removed;
5. whitespace collapsed;
6. standalone `fc`, `cf`, `afc`, `calcio` removed;
7. token `utd` -> `united`;
8. token `st` -> `saint` only when it is a standalone token;
9. no fuzzy matching, edit-distance search, manual team alias addition or outcome-assisted mapping in this run.

A target team maps only if its normalized Footiqo name has exactly one normalized FotMob team identity within the same frozen league asset family. Ambiguous or missing mappings are ineligible.

## Historical feature eligibility
For each target match, both teams must have at least **8 usable prior FotMob matches** strictly before target kickoff.

For each team use the last **10** eligible historical matches (or all available if 8–9), equally weighted; no half-life or recency hyperparameter.

For a team, compute exactly these 8 chance-state quantities:
1. own xG per match mean;
2. opponent xG per match mean;
3. own shots per match mean;
4. opponent shots per match mean;
5. own xG per shot pooled across the window;
6. opponent xG per shot pooled across the window;
7. own high-quality chances (`shot xG >= 0.20`) per match;
8. opponent high-quality chances (`shot xG >= 0.20`) per match.

The target feature vector is exactly 16 values: the eight home-team quantities followed by the eight away-team quantities. No feature subset selection, interaction search or variance/tail-threshold search is allowed after target labels are opened.

## Market anchor
Require valid two-sided Footiqo closing O/U2.5 odds with `O25>1` and `U25>1`.

De-vigged over probability:
`q_over25 = (1/O25) / ((1/O25) + (1/U25))`.

This single scalar is the only market anchor supplied to the first N18 model. H/D/A, BTTS and other O/U lines may remain in the zero-label receipt for audit but are **not model inputs** under the first N18-C contract.

Footiqo provides closing-odds semantics rather than original tick timestamps; therefore the eventual experiment is classified research-grade coarse PIT, not pristine timestamp-level confirmation.

## Source-overlap gate
Construct source identity keys from the complete consumed FotMob history as:
`UTC calendar date | normalized home team | normalized away team`.

Construct target keys analogously from Footiqo local date/team identity.

Required target/source exact normalized identity overlap = **0**. The date buffer should make this mechanically true; any nonzero overlap terminal-stops N18B.

## Cohort selection and split
After all zero-label gates:
1. filter to frozen date/season/league window;
2. require unique target identity, valid O/U2.5 and both-team history eligibility;
3. sort by parsed match datetime ascending, then Footiqo integer id ascending;
4. require at least **550** eligible matches;
5. retain the first exactly **550**;
6. first **400** = `DEVELOPMENT`;
7. final **150** = `CONFIRMATION_SEALED`.

No hash/random selection; split is strictly chronological.

If fewer than 550 pass, terminal `STOP_COVERAGE`; do not extend dates, lower 8-match history gate, add leagues, alter name mapping or reduce confirmation size in this run.

## Required outputs
- `c072n18b_target550_zero_label.jsonl.gz` — target identity, market `q_over25`, fixed 16 historical features, split only;
- `c072n18b_dev400_ids.txt`;
- `c072n18b_confirmation150_ids.txt`;
- `c072n18b_team_mapping.json`;
- `c072n18b_summary.json` — source/target coverage, hashes, overlap, feature completeness and no-target guards.

Do not upload raw Footiqo result/stat tables or raw FotMob CSVs.

## PASS gate
`PASS_N18B_ZERO_LABEL_TARGET_MARKET_JOIN` requires all:
1. exact six Footiqo odds pages resolve with exact 22-column odds table;
2. target/result columns requested/materialized = 0;
3. source/target normalized identity overlap = 0;
4. at least 550 target rows have valid O/U2.5 and both teams mapped with >=8 prior source matches;
5. selected exactly 550 = dev400 + confirmation150 chronologically;
6. all selected rows have finite 16-feature vector and `0<q_over25<1`;
7. model_fit=0, target_score=0;
8. C070-F and all existing sealed reserves remain untouched;
9. C073-C077 scientific results unused.

## Next only after PASS
Freeze N18-C model family, optimization, regularization and proper-score gates **before** opening development outcomes. Confirmation150 remains label-sealed until and unless N18-C development passes its frozen gate.
