# R44L2 首发真实阵型 × 逐场角色 × 机会量：零标签门预注册

## 1. 研究身份

- study_id: `r44l2_lineup_role_opportunity_300`
- project series: `R44L2`
- scope: research only
- formal_weight: `0`
- relation to prior evidence: independent follow-up to `lineup_opportunity_300_r1`; R1 remains immutable negative evidence and is not tuned or overwritten.
- frozen background PR #176: out of scope; MUST NOT be executed, modified, merged, or used as this study's result carrier.

## 2. Scientific question

R1 tested a coarse FPL `GK/DEF/MID/FWD` shell plus XI continuity and prior-only player xG/xA aggregates and failed its OOS gate. R44L2 asks a narrower new question:

> Does actual match formation plus match-specific starting-lineup position structure contain enough incremental information to justify a new opportunity/xG OOS test, beyond the coarse FPL shell already rejected by R1?

This zero-label stage does **not** estimate effects, train a model, read xG/xA outcomes, or inspect football-result labels.

## 3. Frozen sources for zero-label stage

### FPL fixture identity only

- repo: `vaastav/Fantasy-Premier-League`
- pinned commit: `8c97b2adb123863c3dd581e730f1360e89815ac2`
- allowed files in zero-label stage:
  - `data/2025-26/fixtures.csv`
  - `data/2025-26/teams.csv`
- forbidden in zero-label stage:
  - GW / merged GW player performance files
  - expected_goals / expected_assists / expected_goal_involvements
  - goals / final-result fields for scoring or filtering

The script may read only fixture identity fields needed to reconstruct the same latest-300 fixture set: fixture id, kickoff timestamp, home team id, away team id. It MUST NOT use score/result columns to select or filter matches.

### Transfermarkt public dataset

- upstream repo: `dcaribou/transfermarkt-datasets`
- schema/source-code pin: `154367dfa6d6eb0b86332e332f9df0a080c7ddce`
- public files:
  - `games.csv.gz`
  - `game_lineups.csv.gz`
- runtime SHA-256 of the exact downloaded bytes MUST be recorded because the public R2 CSV payload can refresh independently of the source-code commit.
- required fields:
  - games: `game_id`, `competition_id`, `season`, `date`, `home_club_id`, `away_club_id`, `home_club_name`, `away_club_name`, `home_club_formation`, `away_club_formation`
  - lineups: `game_id`, `club_id`, `type`, `player_id`, `player_name`, `position`

## 4. Frozen sample identity

- competition: 2025-26 English Premier League only.
- sample definition: sort the FPL season fixtures by `(kickoff_time, fixture_id)` and keep the last 300 fixtures.
- expected first/last period should match R1's previously frozen latest-300 window; the gate does not use outcomes to enforce this.
- expected rows after expansion: exactly 300 fixtures and 600 team-match records.
- same fixture must remain intact; no team-match may be sampled independently.

## 5. Identity matching rule

Transfermarkt match identity is matched to FPL by:

1. calendar date derived from FPL kickoff timestamp;
2. canonical home-team identity;
3. canonical away-team identity.

Only deterministic, predeclared name aliases are allowed. No outcome, score, xG, xA, odds, or post-match event may be used to resolve an ambiguous match.

If multiple Transfermarkt matches match the same `(date, home, away)` identity, or one Transfermarkt match maps to multiple FPL fixtures, the affected fixture is an identity failure.

## 6. Required zero-label coverage gate

The gate passes only if **all** are true:

- FPL selected fixtures = 300 unique fixtures;
- Transfermarkt fixture identity matched = 300/300;
- matched team-match = 600/600;
- every team-match has exactly 11 unique starting players;
- every starter has a non-empty match-specific `position`;
- every team-match has a non-empty formation;
- no duplicate `(game_id, club_id, player_id)` among starting players after exact-row deduplication;
- no conflicting duplicate starter identities;
- no score, xG, xA, betting price, final result, or other target label is loaded into the audit calculation;
- source byte SHA-256 values are emitted;
- observed formation and position-category counts are emitted for schema freezing only.

There is **no reduced-sample fallback**. If any required fixture fails identity/formation/11-position completeness, terminal status is:

`STOP_R44L2_LINEUP_ROLE_COVERAGE_INCOMPLETE`

and the model stage is not authorized.

## 7. PIT / formal boundary

These historical lineup records do not have a verified pre-match `available_at` timestamp. Existing repository evidence marks this family as `date_only_surrogate_not_pit` / `pit_eligible=false`.

Therefore:

- zero-label pass does not make the inputs formally PIT-eligible;
- any later positive model result can at most be `RETROSPECTIVE_SIGNAL_PASS`;
- formal_weight remains `0` unless a separate future PIT-compliant promotion process succeeds.

## 8. No post-hoc rescue

Forbidden after this preregistration:

- reducing 300 fixtures to a convenient subset after seeing coverage;
- using match outcomes to repair identity;
- replacing missing positions by guessed tactical roles;
- guessing LCB/RCB or other side-specific roles when source position does not provide them;
- inventing set-piece responsibility;
- changing the gate because one club/formation/position category is inconvenient.

If the zero-label gate passes, an **execution freeze must be committed before labels/model scoring are enabled**, fixing the observed role taxonomy, feature map, baseline, candidate, folds, estimator, scores, bootstrap and promotion gate.
