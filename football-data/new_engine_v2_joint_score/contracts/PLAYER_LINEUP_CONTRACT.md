# V2 Player and Lineup Contract

Status: FROZEN BEFORE IMPLEMENTATION

## Lineup states
Exactly one: `EXPECTED_LINEUP`, `CONFIRMED_LINEUP`, `LINEUP_UNKNOWN`.

`EXPECTED_LINEUP` is a probability distribution, never a fact. It contains player identity, position, start probability, expected minutes distribution, availability evidence and known_at. Prediction marginalizes over multiple plausible lineups; it must not select a single pseudo-confirmed XI.

`CONFIRMED_LINEUP` is permitted only when the source has a verifiable publication timestamp strictly earlier than the prediction cutoff. The source URL/hash and observed_at/known_at must be in the feature receipt.

`LINEUP_UNKNOWN` uses team/competition priors and explicitly inflates uncertainty.

## Player state
Allowed components when PIT-lawful data exists: attack contribution, defensive contribution, goalkeeper contribution, position, expected minutes, injury/suspension state, replacement quality, same-position coverage, bench attack/defence quality, and combination penalties/interaction for simultaneous absences. Player effects are regularized toward position/team/competition priors; sparse players cannot receive unconstrained large effects.

## Historical actual lineups
Actual historical XI/minutes may update player state for fixtures strictly after the match once the information is historical. They may not be retroactively used as prematch inputs for that same historical target unless publication known_at before target cutoff is proven.

## Schema / default deny
Allowed top-level keys are explicitly enumerated by implementation. Unknown keys or nested keys fail closed. No result, score, settlement, actual-substitution, postmatch-rating, xG created after cutoff, or match-report field is accepted in prematch input.

## Ablation
Player/lineup layer enters the retained candidate only if it improves pre-final strict-time outer-fold aggregate metrics and does not materially worsen calibration, draw/score diagnostics, worst n>=100 group, or coverage. Otherwise it is removed and reported as `REJECTED_ABLATION`; unavailable trustworthy PIT data is `BLOCKED_DATA`.