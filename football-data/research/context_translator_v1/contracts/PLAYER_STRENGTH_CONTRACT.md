# PLAYER_STRENGTH_CONTRACT

Status: FROZEN_CONTRACT / RESEARCH_ONLY

## Output vector per player
- shot_generation
- finishing
- chance_creation
- passing_progression
- carrying_progression
- possession_retention_risk
- pressing
- tackling_interception
- defensive_position_protection
- aerial
- set_piece
- goalkeeper_shot_stopping
- goalkeeper_sweeping
- goalkeeper_cross_claiming
- goalkeeper_distribution
- on_ball_contribution
- off_ball_contribution
- current_form
- uncertainty

## Required estimator components
A player capability dimension may be marked IMPLEMENTED only when its estimator combines, where the source data supports the dimension:
1. event action value attributable to on-ball/off-ball actions;
2. regularized Adjusted Plus-Minus or an explicitly equivalent possession/segment impact estimator controlling teammate/opponent context;
3. dynamic hierarchical shrinkage across player, role/position, team, league and time.

Mandatory corrections: minutes/exposure, possession opportunity, teammates, opponents, role/position, league strength, recency decay and sample size. Goalkeeper dimensions use goalkeeper-specific event opportunities.

## Dynamic state
Player state is time-indexed. Transfers/loans preserve permanent player_id but change team/league context. Position/role may vary by regime. Cross-league migration applies league-strength prior and elevated uncertainty. Youth/debut/cold-start players inherit role/team/league priors with high uncertainty until evidence accumulates.

## Prohibited proxies
Transfer value, salary, game ratings, fantasy ratings, media ratings, reputation, manually assigned stars or subjective impressions may not directly determine capability values.

## Regularization and leakage rules
Hyperparameters are selected only on development data preceding evaluation. Target-match events/minutes/ratings are forbidden. Prior-match data enter only after PIT availability. Missing event dimensions do not receive synthetic values; layer coverage degrades or becomes BLOCKED_DATA.

## Combination-effect rule
Multi-player interaction effects may be fitted only when preregistered minimum support is met in development data; otherwise lineup aggregation uses regularized additive contributions plus uncertainty. No target-evaluation label may decide whether a combination term exists.

## Audit output
Each vector must carry player_id, team_id, role distribution, effective sample/exposure, state timestamp, feature-source SHAs, estimator-version SHA, uncertainty and coverage grade.