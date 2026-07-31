# E3g-0 External PIT Increment Value Screen Contract

## Status

- Research-only pure 90-minute H/D/A isolation track.
- `formal_weight=0`.
- Draft PR only; do not merge.
- Select exactly one external PIT source.
- No automatic E3g-1, second source, formalization or expansion.

## CURRENT gate

The stage runs only under CURRENT V5.0.2, where pure H/D/A research:

- does not generate score, total-goal or BTTS outputs;
- is not constrained by those output gates;
- does not fall back to Champion because they are not run.

## Frozen identities and baselines

- Fixed full identity and labels: 6,251 matches.
- Fixed B100.
- Market H/D/A probabilities are frozen.
- Champion and E3e through E3f-2A OOF predictions are frozen.
- No match replacement, favourable-league selection, label rewrite, scope change,
  difficult-match deletion or baseline re-optimization is allowed.
- Incomplete external coverage may only be compared on the fixed matched intersection.

## Single source

The only source screened is StatsBomb Open Data (`hudl/open-data`):

- priority: real historical event/xG information;
- cost: free public research data;
- pilot candidate: 1. Bundesliga 2023/2024, competition_id 9, season_id 281;
- source files remain external; only URLs, response metadata and SHA-256 hashes
  are written to the isolated research artifact.

No paid API, subscription, website scraping or second source is allowed.

## PIT hard gate before training

The audit must record:

- provider and repository;
- competition_id and season_id;
- fixed match identity intersection;
- observed_at;
- source match_available/match_updated and per-match last_updated;
- whether a first per-match available_at exists;
- season and league coverage;
- match count and missing rate;
- non-random strong/weak-team missingness;
- source URLs and SHA-256;
- revision risk and attribution terms;
- same-match post-event leakage prohibition.

Target matches may use only information demonstrably available before kickoff.
If historical pre-match availability cannot be proven, status is
`PIT_UNVERIFIED_RESEARCH_ONLY`; no model fit is allowed.

## Low-cost pilot selection

The league/season is selected only because it is the first-priority event/xG
source with a current frozen-sample overlap. It is not selected by predictive
performance.

A usable pilot must have enough unbiased consecutive league targets for rolling
OOF. A one-club curated subset is coverage failure, not a valid league pilot.

## Model stage

Only after identity, timestamp and coverage gates pass may the following be fit:

1. raw market draw probability;
2. market-offset linear Logistic residual;
3. market-offset nonlinear GAM/tree residual.

No SMOTE, class weights, oversampling, artificial draw uplift, post-hoc threshold,
target-season selection or random split is permitted.

For this audit run, model fitting must remain zero if any PIT gate fails.

## Allowed verdicts

Exactly one primary verdict:

1. `EXTERNAL_PIT_INCREMENT_SIGNAL_FOUND`
2. `NO_STABLE_INCREMENT_OVER_MARKET`
3. `PIT_COVERAGE_INSUFFICIENT`
4. `PIT_IDENTITY_OR_TIMESTAMP_FAILED`
5. `RESEARCH_EXECUTION_FAILED`

A signal verdict requires all PIT gates plus full pre-registered draw and H/D/A
proper-score protections. It is not a promotion PASS.

## Asset protection

- formal model change: 0
- CURRENT change: 0
- formal joint score matrix change: 0
- formal score/total-goal/BTTS module change: 0
- formal data/config change: 0
- formal weight change: 0
- Actions permission change: 0
- automatic commit/push: disabled

## Stop condition

After source selection, PIT/coverage audit and either the matched pilot or a
hard-gate stop receipt, stop for user and Codex review. Do not start E3g-1.
