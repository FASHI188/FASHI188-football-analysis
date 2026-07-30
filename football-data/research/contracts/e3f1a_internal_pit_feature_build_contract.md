# E3f-1A Internal PIT Feature Build Contract

## Status

- Research only.
- Pure 90-minute H/D/A support track.
- `formal_weight=0`.
- Draft PR only; do not merge.
- This stage builds and audits features only. It does not fit or evaluate a candidate outcome model.

## Fixed identities

- Source research base: E3f-0 exact HEAD `de18cb335be8a7d4bba2339997d6afa97702f7ef`.
- Full sample: the frozen 6,251 Big-Five rolling-OOS matches.
- Benchmark: the fixed B100, 20 matches per league.
- Random resampling or sample replacement is forbidden.

## Permitted feature families

### Season-reset task state

Built from completed matches strictly before the target match date:

- home and away matches played;
- home and away points;
- points gap;
- home and away goal difference;
- goal-difference gap;
- home and away points per game with explicit availability flags.

Standings state must be keyed by `(competition, season, team)`. No prior-season points or goal difference may flow into a new season.

### Schedule and fatigue proxies

Built only from prior fixture dates:

- home and away rest days with availability flags;
- rest-day gap;
- matches in the prior 7 days;
- matches in the prior 14 days;
- corresponding home-away gaps.

Travel distance and rotation load are not available and must not be inferred from these fields.

### Historical style proxies

Built only from prior completed-match raw observations:

- rolling 5- and 10-match shots for and against;
- rolling 5- and 10-match shots on target for and against;
- rolling 5- and 10-match corners for and against;
- rolling 5- and 10-match cards for and against;
- explicit observation counts for both teams.

These are descriptive proxies. They must not be labelled xG, chance quality, pressing, possession, formation or tactical truth.

### Historical HT-to-FT response proxies

Built only from prior completed matches:

- half-time lead hold rate and trial count;
- half-time deficit recovery-to-draw-or-win rate and trial count;
- half-time draw finishing as full-time draw rate and trial count;
- explicit availability flags.

Zero-trial rates use a neutral placeholder only with availability=0. The placeholder is not evidence.

## PIT and leakage rules

1. Every match on the same calendar date must be frozen before any result from that date updates a feature state.
2. Reversing the within-day match processing order must produce identical features.
3. Current-match FT result, HT result, shots, shots on target, corners and cards are forbidden feature columns.
4. Historical raw observations may update feature state only after the day has been frozen.
5. Missingness must be represented through counts and availability flags; no target-derived imputation is permitted.
6. All produced feature values must be finite.
7. Match identities must remain exactly equal to the frozen 6,251 set.

## Required outputs

- row-level UTF-8 CSV keyed by match identity;
- JSON audit report;
- Markdown audit report;
- full-sample, B100 and per-league availability counts;
- feature schema and feature count;
- same-day order-invariance result;
- forbidden-column audit;
- exact repository HEAD.

## Non-goals

This stage must not:

- train Logistic, tree, GAM or any other candidate model;
- calculate a new draw threshold;
- use class weights;
- modify E3e-0/E3e-1 probabilities;
- create score, total-goal or BTTS outputs;
- modify formal model, data, config, CURRENT or formal weights;
- issue a promotion receipt.

## Stop condition

After feature construction and coverage audit, stop. The next permissible stage is E3f-1B external PIT source and timestamp contracting. No new H/D/A OOF experiment is authorized by E3f-1A alone.
