# COACH_TACTICAL_CONTRACT

Status: FROZEN_CONTRACT / RESEARCH_ONLY

## Coach regime
Every verified head-coach change creates a new regime effective no earlier than its PIT-valid known_at/effective match boundary. A new regime must not inherit the predecessor's full tactical parameter vector. It starts from hierarchical team/league/coach-history priors with elevated uncertainty and learns forward in time.

## Learned regime dimensions
- formation distribution
- tempo
- high press intensity
- defensive line height
- passing directness
- attacking width
- transition attack
- set-piece attack/defence
- leading-state contraction
- trailing-state risk expansion
- substitution timing/tendency

All dimensions must be estimated from permitted historical structured events/lineups/segments. Narrative labels such as "attacking coach" cannot directly become numeric features.

## Tactical matchup dimensions
The V1 fixed matchup set is:
- high press vs buildup resistance
- wide penetration vs fullback/wide protection
- aerial attack vs aerial defence
- transition/counterattack vs high defensive line
- possession attack vs low-block defence
- set-piece attack vs set-piece defence
- striker role/type vs centre-back pairing profile

Matchup effects must be learned from development history with regularization/hierarchical shrinkage. No manually assigned matchup bonus/penalty is permitted.

## State and uncertainty
Each regime stores coach_id, team_id, regime_start, regime_end_if_known, evidence count/exposure, learned vector, covariance/uncertainty, source SHAs and maximum known_at. Coach identity ambiguity is default-deny.

## Leakage boundary
Target-match realized formation, tactical changes, substitutions, red cards or event sequence cannot update the pre-match regime. If confirmed starting formation is not explicitly published pre-cutoff, it remains a scenario variable rather than a known fact.

## Ablation
Coach-regime and tactical-matchup effects are separately switchable for ablation. If adequate PIT-valid event/lineup data are unavailable, mark BLOCKED_DATA rather than infer from results alone or subjective reports.