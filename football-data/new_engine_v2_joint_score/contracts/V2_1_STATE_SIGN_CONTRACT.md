# Football3 V2.1 State Definition and Sign Contract

Status: FROZEN_BEFORE_IMPLEMENTATION

## Competition state
For each competition, keep decayed PIT residual sufficient statistics around global home/away goal priors. The published prediction uses log competition_home_rate and log competition_away_rate as the two venue intercepts, once each.

## Team state
Each competition-team state contains attack_residual_sum, defence_residual_sum, evidence, last_cutoff and season bookkeeping. Attack state > 0 means stronger attack and can only increase that team's mu. Defence state > 0 means stronger defence and can only decrease opponent mu.

The common-scale residual observations are defined against the model's own pre-match expectation:
- home attack residual = home_goals - pre_match_mu_home
- away attack residual = away_goals - pre_match_mu_away
- home defence residual = pre_match_mu_away - away_goals
- away defence residual = pre_match_mu_home - home_goals

Thus positive defence residual means fewer goals conceded than expected. Residuals are converted to bounded log-state increments using one common competition-scale denominator; no home/away-specific team normalization is permitted.

## Venue-team ablation state
If implemented, home/away team venue deviations are separate zero-prior states with stronger shrink than attack/defence. They are identifiable only as deviations around the already-present competition venue intercepts. Their disabled/default state is exactly 0.

## Invariants
1. Equal zero-state teams predict mu_home=competition_home_rate and mu_away=competition_away_rate.
2. Increasing home attack cannot reduce mu_home or change mu_away.
3. Increasing away defence cannot increase mu_home or change mu_away.
4. Increasing away attack cannot reduce mu_away or change mu_home.
5. Increasing home defence cannot increase mu_away or change mu_home.
6. Swapping teams swaps only team-indexed terms plus the explicitly fixed competition home/away intercepts/optional venue-deviation roles.
7. The competition venue advantage is never multiplied, divided or exponentiated again through team strength.