# Football3 New Engine V1 — Engine Design Contract

Status: PREREGISTERED_BEFORE_IMPLEMENTATION
Anchor commit: `7c1815c47102412e88f72189e2b8f837d9b73a42`
Branch: `football3/new-engine-v1`

## Objective
Build a genuinely independent football prediction core. V500/R43 remains immutable and is used only as an external comparison baseline. No V500/R43 fitted parameter, prediction matrix, model output, adapter-renamed algorithm, or legacy output-as-label may enter the New Engine core.

## New core architecture
The pure-football engine will be a new hierarchical dynamic goal model implemented under `football-data/new_engine_v1/`.

1. Dynamic attack and defence: each canonical team has independent latent attack and defence state updated only after an eligible match becomes known. State lives on log-rate deviations from competition priors.
2. Home/away effect: competition-level home and away scoring baselines are separately estimated with shrinkage to a global prior.
3. Time and season decay: latent evidence decays exponentially in elapsed time. Long inter-season gaps trigger additional shrinkage toward the relevant hierarchy.
4. Cold start: prediction fallback order is team-in-competition state -> prior cross-competition team state -> competition state -> global state. New leagues and zero-match teams always receive a valid hierarchical prior rather than an arbitrary constant. Uncertainty increases as effective sample size falls.
5. Observation model: independent Poisson home/away goal counts conditional on latent rates; a normalized full score matrix is emitted. 1X2 is derived only by summing that score matrix.
6. Uncertainty: effective sample sizes and Gamma-Poisson rate uncertainty are propagated to explicit rate intervals and a scalar uncertainty score/source class.
7. Strict batching: all fixtures sharing an exact kickoff timestamp are predicted and frozen before any of those fixtures update state.
8. Fail-closed: ambiguous/missing identity, duplicate fixture key, invalid or non-monotonic time, malformed goals, future-known observation, corrupted probability/matrix or time conflict raises and produces no prediction.

## Pure and market-assisted separation
`pure_engine.py` is not permitted to import, parse, accept, inspect, or infer odds/market fields. `market_assist.py` is a separate downstream adapter that can consume an already-frozen pure prediction plus a verified prematch market snapshot. Their outputs and metrics are separate. Absence of a verified PIT market snapshot must result in `MARKET_ASSIST_NOT_SCORED`, never backfill.

## Scientific status
Engineering success is not scientific promotion. The final holdout gate is preregistered separately. A failed scientific gate must be recorded as `NOT_PROMOTED`; only a complete gate pass may write `MODEL_CANDIDATE_PASSED`. Neither state changes CURRENT or the formal model.

## Final project state
Completion of this authorized rebuild writes only the branch-local handoff state `GPT_REBUILT_PENDING_CODEX_RECHECK`.