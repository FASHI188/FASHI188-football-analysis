# Football3 V2.1 Base Design Contract

Status: FROZEN_BEFORE_IMPLEMENTATION
Base branch: football3/new-engine-v2-joint-score-clean
Base exact HEAD: 9a03c3aaab5d1f095d53eabd64fd03018850ed13
Repair branch: football3/new-engine-v2-1-base-repair-v1

## Scientific scope
V2.1 repairs only the pure-football team dynamic-strength and venue structure. Translator, player, lineup, coach, market, odds, Provider, Secret and target-match postmatch inputs are excluded. Phase-1 score family is exactly INDEPENDENT_POISSON_FROZEN.

## Mean structure
log(mu_home) = competition_home_intercept + home_team_attack - away_team_defence + optional_home_venue_bias(home) + optional_away_venue_bias(away)
log(mu_away) = competition_away_intercept + away_team_attack - home_team_defence + optional_away_venue_bias(home) + optional_home_venue_bias(away)

The competition home and away intercepts each enter exactly once. Team attack and defence live on one common log-residual scale and are never separately divided by venue-specific league rates. Defence sign is positive=stronger and therefore is subtracted from opponent log mean.

## Optional venue-team bias layer
Allowed only as an independent, explicitly ablatable, zero-centred and strongly shrunk layer. Default enabled value is zero unless development-only selection preregistered below chooses a nonzero shrink configuration. It must not absorb or reapply league-level home advantage.

## Hierarchy and cold start
Competition intercepts shrink to global home/away priors. Team attack/defence states shrink to zero. New/promoted/zero-sample teams therefore return through competition to global priors. Cross-season state shrink is applied exactly once on first view/update in a new season; repeated views in the same season must be idempotent.

## Matrix contract
Only INDEPENDENT_POISSON_FROZEN is permitted. Matrix cells must be finite and nonnegative, normalized to 1, and 1X2 must be integrated from that same matrix. No Dixon-Coles, diagonal inflation, NB, Mar-Co/Sarmanov, separate 1X2 head or market projection is allowed in V2.1 phase 1.

## Governance labels
This work is RESEARCH_ONLY and POST_VIEW_DIAGNOSTIC where 2023/24-2025/26 labels are used. It can end only as V2_1_BASE_REPAIR_REJECTED or V2_1_BASE_REPAIR_ENGINEERING_PASS_POSTVIEW_ONLY. It is never MODEL_CANDIDATE_PASSED, SCIENTIFIC_PASS or formally enabled.