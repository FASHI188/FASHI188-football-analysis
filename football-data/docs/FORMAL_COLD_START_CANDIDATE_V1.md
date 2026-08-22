# Formal cold-start candidate v1

## Status

`ENGINEERING_RESEARCH_ONLY_FORMAL_WEIGHT_0`

This is not a production artifact, scientific promotion, or change to the unique
formal CURRENT. It is an opt-in, explicit-history engineering candidate. The
hash-bound `football_v460_engine.py`, its formal configuration, the 17 existing
domain artifacts, and all routine formal entrypoints remain unchanged.

## State machine

- `STABLE_CURRENT_SEASON`: at least 30 current-season competition matches, at
  least two home-venue samples for the home team, and at least two away-venue
  samples for the away team. The candidate delegates to the unchanged formal
  history engine and uses no cold-start prior.
- `PRIOR_SEASON_SHRINKAGE`: the stable gate is not met and an explicit
  competition/target-season prior plus a separately hash-binding PASS receipt is
  supplied. Team identity must exist in that prior. Competition and venue prior
  weights are deterministic and decrease to zero as valid current samples reach
  the unchanged gates.
- `GENERIC_VALIDATED_FALLBACK`: no prior-season artifact is supplied and an
  explicit competition/season-scoped generic artifact plus PASS receipt is
  supplied. Both teams must be present in its validated identity scope. It uses
  a neutral competition baseline and does not invent team strength.
- `UNINFORMED_GLOBAL_BASELINE`: when neither a validated prior nor a validated
  generic artifact exists, an explicit versioned global baseline still returns
  one internally coherent score distribution. It contains no competition or
  team-strength evidence, is always VERY_LOW confidence, and is coverage-only.
- `HARD_FAIL`: missing evidence, partial artifact/receipt pairs, invalid status,
  hash/version/scope/time mismatch, unknown team, future/same-day row, wrong
  season/competition, malformed input, or an unvalidated parameter set.

An invalid prior is never silently downgraded to the generic or global fallback. Callers
must omit the prior entirely before the generic route can be considered.

The default CLI/router is `engine/run_universal_prediction_candidate_v1.py`.
It attempts the unchanged formal V460 route first. Ordinary coverage gaps may
downgrade, but hash, receipt, schema, validation-report and other integrity
failures remain hard errors. Therefore “all matches receive a distribution” does
not turn corrupted formal assets into apparently valid predictions.

## Artifact boundary

Artifacts are explicit in-memory inputs. Their independent receipt binds:

- schema and artifact type;
- version;
- canonical artifact SHA256;
- receipt SHA256;
- validation status `PASS`;
- validation timestamp no later than the prediction cutoff;
- exact competition and target-season scope;
- a complete parameter set.

Tests use synthetic artifacts only. This change does not create, validate, or
promote any real prior or generic production artifact.

The global baseline values are explicit in the candidate config and are not
claimed to be trained or competition-specific. They solve output coverage only;
they do not establish useful accuracy for an unseen league or team.

## Continuity and output labels

The competition prior weight is `max(0, (30-n)/30)`. Venue components use
`max(0, (2-n)/2)`, with the maximum required component reported as the effective
prior weight. At the 30-match boundary the stable route has prior weight zero.
The test contract bounds the synthetic 29-to-30 1X2 jump at 0.10 absolute.

Every result from this candidate is labeled:

- `formal_weight = 0`;
- `exact_gate = false`;
- `ev_decision = No Bet`;
- `scientific_status = NOT_VALIDATED_NOT_PROMOTED`;
- `production_activation = false`.

Engineering tests cannot establish improved accuracy. Real chronological OOS
validation, frozen real artifacts, independent replay, promotion receipts, and a
separate user decision would be required before any formal activation.
