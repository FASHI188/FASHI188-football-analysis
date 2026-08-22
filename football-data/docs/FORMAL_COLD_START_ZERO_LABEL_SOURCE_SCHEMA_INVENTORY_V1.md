# Football3 formal cold-start zero-label source/schema inventory v1

## Status

`INSUFFICIENT_PIT_COVERAGE`

This is a zero-label inventory result only. It does not authorize opening outcome
labels, training, tuning, real-match scoring, provider access, production artifact
generation, scientific promotion, betting, publishing, merging, Airtable writes,
or changes to PR #334/R5.

## Frozen anchors

- Repository: `FASHI188/FASHI188-football-analysis`
- Branch: `football3/formal-cold-start-v1`
- Inventory base HEAD: `4fb8218d0a1378261e874cecb266fdf2ac770810`
- Frozen plan: `football-data/docs/FORMAL_COLD_START_FROZEN_OOS_PLAN_V1.md`
- Frozen plan blob SHA: `819f0111b9df9864e647fa0fcfeeebee019a3241`
- Required cohort: at least 200 fixtures, at least 8 competition-season folds,
  at least 20 eligible fixtures per fold, fixed T-60m freeze.

## Evidence boundary

Only source policy, schemas, templates, engine/validator code, identity policy,
and aggregate readiness manifests were inspected. No forward event record,
result, outcome, score, post-match audit, or provider payload was opened.

## Read-only inventory

| Repository evidence | What it establishes | Frozen-plan gap |
| --- | --- | --- |
| `football-data/schemas/market_snapshot.schema.json` | Supports 1X2, AH, OU and source observations | Does not require all three surfaces, two independent source groups, collection timestamp, or raw content hash; source minimum is one |
| `football-data/templates/match_input.example.json` | Demonstrates a complete single-match 1X2/AH/OU example with two named source groups | No season field, collection timestamp, or raw content hash |
| `football-data/config/current_market_public_source_registry_v527.json` | Defines fail-closed public-source routing and warns that aggregator copies are not independent | Only OddsPortal is conditionally capable of a complete current snapshot; registry entries are routes, not acquired historical evidence |
| `football-data/config/current_season_team_identity_v5524.json` | Provides identity-only current-season mappings and prohibits fuzzy cross-club substitution | Does not prove historical identity coverage across eight competition-season folds |
| `football-data/validation/market_lomo_data_readiness_v470.py` | Counts complete timestamped 1X2/AH/OU rows and requires 200 rows before review | Does not test collection timestamp, two-source independence, raw hash/replayability, T-60m eligibility, or per-fold minimums |
| `football-data/manifests/market_lomo_data_readiness_v470_status.json` | Aggregate zero-label manifest for 17 competition domains | Reports 0 production-validated domains, 0 timestamped-complete domains ready for review, 0 formal-EV domains; 16 domains require credential-gated backfill and 1 has research-only evidence with 0 usable rows |

## Determination

The repository does not contain a zero-label eligibility ledger or aggregate
manifest that can prove the frozen minimum cohort. The existing readiness
manifest positively reports zero formally eligible historical coverage.
Therefore the only permitted result under the frozen plan is:

`INSUFFICIENT_PIT_COVERAGE`

This result is not a model-quality failure and is not evidence that the engine is
less accurate. It means scientific OOS comparison cannot legally begin from the
currently proven point-in-time evidence.

## Exact next allowed action

A separate authorization may permit acquisition or import of a zero-label
eligibility ledger containing, for every candidate fixture:

- competition, season, canonical home/away identities, kickoff UTC;
- quote observation time and collection time;
- complete 1X2, AH and OU surfaces at the frozen T-60m cutoff;
- at least two genuinely independent source groups;
- immutable raw reference and content SHA256;
- engine/config/prediction hashes;
- no outcome or post-match fields.

Before any label access, that ledger must independently prove at least 200
eligible fixtures, at least 8 competition-season folds, and at least 20 fixtures
per fold. Thresholds must not be relaxed.

## Non-actions

- Labels opened: 0
- Training/tuning/scoring: 0
- Provider/secret access: 0
- Production artifacts: 0
- Model or engine modifications: 0
- Airtable writes: 0
- Push outside this branch: 0
- PR creation/merge/unlock: 0
- PR #334/R5 changes: 0
