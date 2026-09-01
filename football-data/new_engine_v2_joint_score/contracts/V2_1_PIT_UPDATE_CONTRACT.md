# Football3 V2.1 PIT and Update Contract

Status: FROZEN_BEFORE_IMPLEMENTATION

## Prediction-before-update
Every target fixture is predicted from state containing only labels whose result_available_at is <= target cutoff and whose own cutoff is strictly earlier. All fixtures sharing the exact same cutoff are frozen as one batch before any label from that batch can update state.

## Residual update
For every released fixture, the update must use the exact pre-match mu_home and mu_away that were frozen before its outcome was known. Raw GF/GA accumulation is prohibited for team attack/defence. Team updates use opponent-adjusted residuals: actual_goals - pre_match_expected_goals for attack and pre_match_expected_goals - actual_goals_conceded for defence.

Competition intercept statistics may update from observed home/away goals only after release because they estimate the competition venue baseline, but team states must be residualized against the full pre-match expectation.

## Release queue
Updates are applied in deterministic (result_available_at, cutoff, competition_id, fixture_id) order. No outcome with release after the current prediction cutoff is visible. A future cutoff, duplicate fixture, invalid identity, negative/non-integer goal label, missing label for an applied batch, duplicate team within the same kickoff batch, or a team scheduled twice at the same cutoff must fail closed.

## Cross-season transition
A competition-team state crossing from season S to S+1 is shrunk exactly once before its first S+1 prediction/update. The transformed state records the new season so repeated predictions before an update cannot shrink it again. Shrink is monotone toward zero for attack/defence/venue deviations and toward the hierarchical prior for evidence.

## Determinism
Canonical serialization must preserve parameters, competition state, team state, transition markers, release bookkeeping and frozen pre-match expectations. Serialize -> restore -> predict must yield byte-identical canonical prediction payloads.

## Leakage boundary
Pure-football V2.1 must not request or read odds, market prices, provider credentials, secrets, target-match final score/result before scorer phase, ratings, events, substitutions, technical statistics, player/lineup/manager data, or any postmatch target field.