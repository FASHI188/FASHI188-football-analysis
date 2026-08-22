# Football3 universal cold-start frozen OOS evaluation plan v1

## Status

`PREREGISTERED_NOT_AUTHORIZED_NOT_RUN`

This document freezes an evaluation design only. It does not authorize opening
outcome labels, training, tuning, real-match scoring, provider access, production
artifact generation, scientific promotion, betting, or merging.

## Frozen engineering anchor

- Repository: `FASHI188/FASHI188-football-analysis`
- Branch: `football3/formal-cold-start-v1`
- Engine anchor before this plan: `058760b3711f0a734b34d55764da83d55fda3bda`
- Remote acceptance run: `32556936211`
- Remote acceptance result: 123/123 engineering tests PASS
- Formal V460 core, formal configuration, R5/HDA, and PR #334 remain outside this plan.

The engine code, candidate configuration, routing order, market constraints, and
metrics below must remain frozen for the entire evaluation. Any change creates a
new plan version and invalidates comparisons under this version.

## Question

For fixtures that the formal V460 core cannot cover because the competition,
season, or current-season sample is unavailable or immature:

1. does the market-anchored cold-start route improve 1X2 predictive quality over
   the fixed uninformed global baseline;
2. does it remain acceptably close to the de-vigged question-time market baseline;
3. does the projected score matrix remain coherent, probability-conserving, and
   calibrated across competition-season folds?

The evaluation cannot establish betting value because the same market prices are
inputs to the market-anchored candidate.

## Frozen candidates and baselines

All candidates are generated before labels are opened.

- `B0_UNINFORMED_GLOBAL_BASELINE`: the exact versioned baseline in
  `formal_cold_start_candidate_v1.json`.
- `C1_MARKET_ANCHORED_COLD_START`: the frozen global score matrix projected
  using synchronized point-in-time 1X2, Asian handicap, and total-goals surfaces.
- `B1_DEVIG_1X2_MARKET`: the deterministic three-way no-vig probability from
  the same accepted 1X2 snapshot.

No model may be trained. No parameter, threshold, freeze time, exclusion rule,
market source, or metric may be selected after labels are read.

## Cohort contract

The evaluation cohort must be sealed before outcome access and must contain:

- at least 200 eligible fixtures;
- at least 8 competition-season folds;
- at least 20 eligible fixtures in every reported fold;
- explicit emphasis on early-season and previously unsupported competitions;
- one record per fixture, with stable fixture ID and no duplicate team-time pair.

If these fixed minimums cannot be met, the only permitted result is
`INSUFFICIENT_PIT_COVERAGE`; thresholds must not be relaxed.

## Point-in-time input contract

Each sealed row must contain, before outcome access:

- competition ID and season;
- normalized home and away team identity;
- kickoff timestamp in UTC;
- fixed prediction freeze at T-60 minutes;
- source observation timestamp and collection timestamp;
- complete 1X2, Asian handicap, and total-goals prices;
- at least two genuinely independent source groups;
- raw-record content hash and immutable source reference;
- engine commit SHA, configuration hash, and prediction receipt hash.

A row is ineligible if any required timestamp is missing, any observation is
after the freeze, source independence is false, surfaces are asynchronous beyond
the frozen 900-second limit, identity is ambiguous, or the raw record cannot be
replayed. No nearest future quote, closing-price substitution, or post-match
repair is allowed.

## Zero-label sealing sequence

1. Inventory candidate sources without reading result columns.
2. Build the eligibility ledger using only identifiers, timestamps, market
   surfaces, and source metadata.
3. Freeze the ordered fixture IDs and exclusion reasons.
4. Record canonical SHA256 for the ledger and all raw inputs.
5. Generate and seal B0, C1, and B1 probabilities at the frozen engine SHA.
6. Record prediction receipt hashes.
7. Only after a separate explicit user authorization may an independent scoring
   step open the 90-minute outcome labels.

The prediction process must have no access to final scores, H/D/A labels,
settlement fields, result-derived features, or later market observations.

## Frozen metrics

Primary metric:

- multiclass 1X2 logarithmic loss.

Secondary metrics:

- multiclass Brier score;
- classwise calibration intercept and slope;
- fixed 10-bin expected calibration error;
- H/D/A reliability table;
- score-matrix probability conservation and maximum market-constraint residual;
- fold-level and aggregate coverage.

Uncertainty:

- paired fixture bootstrap with 10,000 replicates;
- fixed random seed `20260822`;
- competition-season fold results reported separately;
- 95% confidence intervals for paired metric differences.

## Frozen decision rules

Engineering validity requires all eligible predictions to conserve probability
within `1e-8`, all accepted market projections to converge within the existing
solver tolerance, and zero PIT, identity, hash, or receipt violations.

Scientific-support outcome `COLD_START_OOS_SUPPORT` requires all of:

1. C1 aggregate log loss is lower than B0;
2. the upper bound of the 95% paired interval for `C1 - B0` log loss is below 0;
3. C1 does not exceed B1 log loss by more than 0.01 in aggregate;
4. no reported fold has a catastrophic C1 log-loss degradation above 0.05 versus B1;
5. calibration does not show a material directionally consistent deterioration
   across a majority of folds.

Otherwise the result is `STOP_NO_CANDIDATE`. A passing result is evidence for
the coverage-only route; it does not by itself change `formal_weight=0`,
`exact_gate=false`, `No Bet`, or production activation.

## Required artifacts for a later authorized run

- zero-label eligibility ledger and exclusion ledger;
- raw-input manifest with hashes and timestamps;
- sealed prediction file for B0, C1, and B1;
- prediction receipt manifest;
- separately opened outcome file;
- metric tables and paired-bootstrap output;
- fold-level calibration report;
- independent replay report;
- final decision receipt containing exactly one permitted terminal outcome.

## Forbidden in this plan phase

- reading or inferring outcome labels;
- training, tuning, threshold search, or source selection using results;
- changing the engine or formal V460 core;
- generating real prediction scores;
- accessing provider secrets;
- creating production artifacts;
- claiming improved accuracy, scientific PASS, model promotion, betting value,
  or Draw solved;
- modifying, unlocking, readying, or merging PR #334.

## Next authorization boundary

The next allowed action is a zero-label source and schema inventory against this
frozen contract. It requires a separate explicit user command. Until then this
plan remains `PREREGISTERED_NOT_AUTHORIZED_NOT_RUN`.
