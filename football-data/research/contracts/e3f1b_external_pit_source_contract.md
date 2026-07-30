# E3f-1B External PIT Source and Timestamp Contract

## Status

- Research and source-governance only.
- Pure 90-minute H/D/A support track.
- `formal_weight=0`.
- Draft PR only; do not merge.
- External record ingestion: 0.
- Candidate model fit: 0.
- Threshold tuning: 0.

## Purpose

E3f-1B determines whether an external source is admissible for future pre-match PIT feature construction. It does not fetch, store, join or train on external football records.

## Fixed target identity

Any future source must be capable of mapping to the frozen 6,251 Big-Five match identities and fixed B100 without changing either sample.

Target competitions and seasons are the five top European leagues represented in the frozen sample. A provider's marketing claim, competition coverage flag or website visibility is not proof of row-level coverage.

## Required admission fields

For every source and feature family, the source contract must establish:

1. provider and exact product or repository identity;
2. access mode and authentication requirement;
3. licensing or contractual reuse terms;
4. competition-season coverage;
5. row-level match and team identifiers;
6. original observation timestamp or version history;
7. proof that the observation existed before the research freeze point;
8. expected full-sample and per-league join coverage;
9. missing and late-publication handling;
10. immutable snapshot or response-hash procedure;
11. correction/version policy;
12. leakage boundary;
13. cost or procurement authorization where applicable.

A source cannot become `PIT_READY` merely because it exposes lineups, injuries, xG, formations or venue data.

## Feature-family requirements

### Lineups, injuries and suspensions

- Expected lineup observations require their own original publication timestamp.
- Official lineups published shortly before kickoff are not equivalent to expected lineups available at an earlier betting freeze.
- A lineup added during or after a match is retrospective and must not be treated as pre-match.
- Injury status must distinguish confirmed missing, questionable and suspension states.
- Later corrections must retain the earlier snapshot rather than overwrite it silently.

### xG and chance quality

- Same-match xG produced after kickoff or after full time is post-match and is forbidden as a current-match feature.
- Historical xG may be used only as prior-match history after completion and same-day batch update.
- Provider xG definitions and revisions must be versioned.

### Tactical and manager data

- Formations derived from actual lineups are subject to the same publication-time rule.
- Manager identity, changes and tactical labels require effective-date history.
- Marketing labels or opaque prediction products are not auditable tactical features.

### Travel and venue data

- Stadium coordinates may support a static travel-distance proxy only after historical home venue, relocation and neutral-site mapping.
- Geospatial license and attribution obligations must be preserved.
- A club's current stadium is not automatically valid for every historical match.

## Source status vocabulary

- `READY_FOR_PILOT_CONTRACT`: licensing and technical structure are potentially usable, but a bounded identity/timestamp pilot is still required.
- `PARTIAL_OPEN_SOURCE`: usable only for the competitions and seasons actually present in an immutable snapshot.
- `PAID_OR_KEY_REQUIRED`: access requires a provider account, token, plan or commercial agreement.
- `TIMESTAMP_NOT_PROVEN`: the audit did not establish an original pre-match observation timestamp.
- `LICENSE_NOT_ESTABLISHED`: the audit did not establish permission for automated historical extraction and redistribution.
- `FRAGMENTED_OFFICIAL_SOURCE`: authoritative observations exist, but no normalized full-history interface or archive contract was established.
- `NOT_READY`: one or more hard admission fields remain unresolved.

## Hard stop

No external source may be joined to the 6,251 rows until a separate source-specific pilot receipt reports:

- exact source snapshot or API response hashes;
- mapped and unmatched identities;
- original observation-time distribution relative to kickoff and research freeze;
- full and per-league coverage;
- duplicate/correction handling;
- licensing approval;
- leakage residual of zero.

## Non-goals

E3f-1B must not:

- download an external historical dataset;
- call a paid API;
- create provider accounts or use API keys;
- scrape websites;
- infer missing timestamps;
- train or evaluate a model;
- activate a threshold;
- modify formal model, data, config, CURRENT or formal weights;
- issue a promotion receipt.

## Stop condition

After the source matrix and admission verdict are signed, stop. A later stage may either run a separately authorized source-specific pilot or proceed to an internal-feature-only ablation experiment. Neither action is authorized automatically by E3f-1B.
