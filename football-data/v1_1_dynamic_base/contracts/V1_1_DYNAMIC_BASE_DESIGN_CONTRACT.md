# Football3 V1.1 Dynamic Base — Design Contract

Status: FROZEN_BEFORE_IMPLEMENTATION_AND_DEVELOPMENT_RESULTS
Base branch: `football3/new-engine-v1`
Base exact HEAD: `22f639304d2e32fc952dbec2255153ee45dcd41a`
Frozen V1 Artifact: `9732754224`
Frozen V1 ZIP SHA256: `5f0af0c428f19492715669c8e4fb2451ee94bf373f17f5560ff1a42114375bcb`
Frozen V1 pure_engine.py SHA256: `cc2c2c3eca421ad6d277107b8f1212656b2e943cc179e7f394ac53e916c3f318`

## Scope
V1.1 is a research-only residual dynamic layer on top of the frozen V1 pure-football engine. The frozen V1 engine bytes, its competition home/away baselines, cold-start hierarchy and independent Poisson score-matrix construction are never modified. V1 remains the sole baseline.

## Dynamic state
V1.1 may add only four venue-specific team components: home attack, home defence, away attack, away defence, plus pooled same-team attack/defence priors used only for shrinkage. Attack and defence remain separate. Home and away states are learned separately. No league home advantage, competition intercept or V1 strength factor is reapplied inside this layer.

For a target match, V1 is first evaluated exactly as frozen. Dynamic updates use only residuals against that frozen pre-match V1 expectation, so opponent strength is corrected by construction. For a goal count y and frozen V1 expected goals mu, the signed innovation is the clipped Poisson score residual `(y-mu)/sqrt(mu+0.25)`. Attack receives this sign; defence strength receives its negative, so higher defence state always lowers the opponent mean.

The V1.1 means are:
`mu_home_v1_1 = mu_home_v1 * exp(beta * (home_attack_home - away_defence_away))`
`mu_away_v1_1 = mu_away_v1 * exp(beta * (away_attack_away - home_defence_home))`
with the correction applied once and no other venue multiplier. The frozen V1 `min_rate`, `max_rate`, score-matrix and 1X2 integration are reused.

## Hierarchical reliability / exact fallback
Each venue component shrinks toward the same-team pooled attack/defence residual and then toward zero. Zero is the exact V1 prior. A component whose effective evidence is below the frozen minimum-evidence rule contributes exactly 0. If all four target components are unavailable/unreliable, the full V1.1 probability payload (`mu`, score matrix and 1X2 values) must be byte-for-byte numerically identical to frozen V1 values apart from V1.1 metadata fields.

New teams, promoted teams and sparse teams therefore inherit V1's existing legal cold start; the residual layer cannot invent strength. Cross-season dynamic residual state is shrunk exactly once at the first prediction/update in a new season.

## PIT/update contract
All fixtures sharing an exact kickoff are predicted and frozen before any member of that kickoff batch is updated. A dynamic update is legal only from a fixture whose V1 and V1.1 pre-match predictions were already frozen and whose `result_available_at <= as_of`. Same/future-cutoff labels, duplicate fixture IDs, duplicate team participation in one kickoff batch, identity changes, invalid goals, time reversal and non-finite state fail closed.

## Prohibited layers
No Dixon-Coles, Mar-Co, diagonal/draw multiplier, NB, Translator, player, lineup, coach, tactical, market/odds, Provider/Secret or result-conditioned patch may enter V1.1. Formal weight remains 0 and formal enablement remains false.