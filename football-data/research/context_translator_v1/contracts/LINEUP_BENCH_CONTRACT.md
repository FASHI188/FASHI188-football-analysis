# LINEUP_BENCH_CONTRACT

Status: FROZEN_CONTRACT / RESEARCH_ONLY

## Three mandatory routes
1. EXPECTED_LINEUP: mixture of multiple plausible starting-XI/bench scenarios with probabilities determined from pre-cutoff evidence only.
2. CONFIRMED_LINEUP: permitted only when an approved source proves publication strictly before cutoff.
3. LINEUP_UNKNOWN: no trustworthy lineup evidence; fall back toward team-level state and increase uncertainty.

## Per-player lineup variables
- player_id
- starting_probability
- availability_probability
- expected_minutes_distribution
- injury_status
- suspension_status
- return_status
- rotation_probability
- role/position distribution
- same-role replacement quality
- uncertainty
- provenance/known_at

## Scenario rules
Scenario probabilities must sum to one and be frozen before the target outcome is known. Multi-absence scenarios are modeled jointly when evidence permits; otherwise use regularized independent/low-order approximations with explicit uncertainty.

Never average player/lineup features first and then build one score matrix. Each lineup scenario produces its own translated context and complete score matrix; full matrices are mixed by scenario probability.

## Bench/substitution scope
Model pre-match bench attack/defence/keeper coverage, critical-position replacement quality, substitute fitness and expected minutes, and coach historical substitution timing/tendency under possible leading/drawing/trailing states. These are probabilistic pre-match hazards only. Target-match actual substitutions, bench usage or realized game state are forbidden.

## Confirmed-lineup boundary
A lineup appearing in a post-match dataset is not retroactively CONFIRMED_LINEUP. Without a timestamped pre-cutoff publication record it remains unavailable for that route.

## Degradation
If player identities, known_at or lineup source confidence fail contract, downgrade to EXPECTED_LINEUP or LINEUP_UNKNOWN as appropriate. Never fabricate players or silently fill a missing XI.

## Audit
Every scenario records scenario_id, probability, route, player/minute assumptions, source SHAs, known_at_max, uncertainty and coverage grade.