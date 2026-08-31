# Football3 Context Translator V1 — TRANSLATOR_MASTER_SCOPE

Status: FROZEN_CONTRACT / RESEARCH_ONLY
Base V2 exact HEAD: 9a03c3aaab5d1f095d53eabd64fd03018850ed13
Branch: football3/context-translator-v1

## Mission
Translator V1 does not directly predict match outcome. It converts strictly pre-kickoff context into auditable scenario-specific adjustments to the V2 home/away scoring intensities, then delegates score-matrix construction, natural draw extraction, 1X2 generation and final consistency projection to the V2 integration layer.

Pipeline: raw pre-match data -> identity resolution -> known_at/PIT validation -> team state -> player dynamic vectors -> lineup/bench scenarios -> coach/tactical regime -> tactical matchup -> fatigue/travel/referee/competition/environment -> pre-match process hazards -> scenario-specific log-mu adjustments -> scenario score matrices -> probability mixture -> 1X2 and exact-score outputs -> minimal-KL consistency projection.

## Frozen V1 functional scope
1. Team dynamic attack/defence, venue/league hierarchy, decay, cross-season shrinkage, promoted/new-league/transfer-window cold start, sample size and uncertainty.
2. Player dynamic capability vectors using event action value + regularized adjusted plus-minus + dynamic hierarchical shrinkage, with minutes/possession/teammate/opponent/role/league/recency/sample corrections.
3. Permanent player identity with alias/rename/transfer/loan/youth/debut/role-change/cross-league handling; ambiguous identity is never auto-merged.
4. EXPECTED_LINEUP, CONFIRMED_LINEUP and LINEUP_UNKNOWN routes with availability, starting probability, expected-minute distribution, injuries, suspension, return, rotation, replacement quality and multi-absence handling.
5. Bench/keeper coverage and pre-match substitution tendency conditional on possible game states; target-match realized substitutions are forbidden.
6. Coach regime boundaries and learned formation/tempo/press/line/directness/width/transition/set-piece/state-dependent risk/substitution traits; new coach starts from hierarchical prior with high uncertainty.
7. Learned tactical matchup effects only; no hand-coded bonus points.
8. 7/14/28-day load, rest, extra time, continental hangover, consecutive away, travel, time zones, player minutes and season phase.
9. Venue/surface/altitude/weather/referee/competition/two-leg/extratime/pre-match standings and verifiable match-state context.
10. Pre-match probabilistic process hazards for 0-30,31-60,61-75,76-90,stoppage when legal minute-level history exists. Target-match realized red cards, VAR, substitutions, injuries and stoppage are realtime-only and forbidden here.
11. Approved-source unstructured extraction is fact-only; language models may structure facts but never assign player strength, win probability or discretionary numeric bonuses.
12. Coverage grades: FULL_TRACKING, FULL_EVENT, LINEUP_STATS, TEAM_ONLY, HARD_FAIL. Missing layers degrade coverage and increase uncertainty; they are never fabricated.

## V2 integration invariant
For every lineup/context scenario independently:
log(mu_home_s)=log(V2_base_mu_home)+home_lineup_attack-away_lineup_defence-away_keeper+coach_tactical_matchup+bench_fatigue+competition_environment.
Away side is symmetric. Scenario features must never be averaged before matrix construction. Each scenario creates a full score matrix, matrices are probability-weighted, then natural draw / 0-0 / 1-1 / 2-2 and an independent 1X2 head are produced, followed by minimal-KL consistency projection.

## Implementation status semantics
IMPLEMENTED: real permitted data enters blind prediction and changes scenario score matrices under PIT rules.
REJECTED_ABLATION: implemented layer fails preregistered acceptance gate.
BLOCKED_DATA: contract exists but legal/PIT-quality data is insufficient.
CONTRACT_ONLY: interface exists but no real permitted data reaches prediction.

## Hard exclusions
No main/CURRENT/Airtable/PR#334/R5 changes. No merge, Ready, force or production enablement. No paid/unapproved provider, Provider/Secret read, target-label leakage, post-match current-fixture data, future data, subjective motivation scores, market value/game/media rating as player ability, or scope expansion beyond FILE_WHITELIST.

V1 scope is immutable after this contract freeze except defect repairs strictly necessary to implement the listed scope. New algorithms/data/features require Translator V2/V3 branches and same-cohort comparison; V1 evidence must remain immutable.