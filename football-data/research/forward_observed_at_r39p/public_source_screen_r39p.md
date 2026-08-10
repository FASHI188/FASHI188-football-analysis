# R39P public/no-key timestamped-source screen

Status: `NO_ACCEPTABLE_EXISTING_PUBLIC_FORWARD_ARCHIVE_FOUND_IN_THIS_SCREEN`

This screen is research governance only. It uses no target labels, no model fit, no provider credential, and no paid request. The requirement is **authentic original pre-match observation time**, not a synthetic leakage-guard timestamp assigned retrospectively.

## Acceptance contract

A source can enter a future R39P model only if it preserves, for each relevant pre-match observation:

- fixture identity and scheduled kickoff;
- original observation/quote/publication timestamp;
- proof that the observation precedes kickoff;
- the underlying lineup/injury/odds payload or a content hash;
- source/provider identity;
- enough repeated historical coverage for time-ordered validation.

A match date, provider fetch date, dataset-build timestamp, or synthetic `known_at = kickoff` is not equivalent to original observation time.

## Candidate: eatpizzanot/soccer-dataset

Source repository: `eatpizzanot/soccer-dataset`, public GitHub/Hugging Face dataset.

Useful facts:
- published tables include fixtures, odds, fixture_lineups, players and player-match records;
- fixtures have kickoff `date_utc`;
- odds have a `known_at` column;
- the dataset describes a leakage guard for post-match facts.

R39P rejection:
- `fixture_lineups` has no `observed_at`, `known_at`, provider publication time, or lineup-announcement timestamp field;
- `odds.known_at` is documented only as `Odds known at/around kick-off (closing line)`, not an original bookmaker quote timestamp;
- `match_stats.known_at` is explicitly a derived leakage guard (`kickoff + 105 min`), demonstrating that at least some `known_at` values are analytical availability assignments rather than source observation timestamps;
- the documented pipeline is a one-time/re-runnable restoration and API-Football historical backfill process, not an append-only forward observation logger.

Decision: **REJECT for authentic observed-at research**. It may be useful as a conventional historical dataset, but it does not satisfy the CURRENT-style forward timestamp contract for lineup or odds observations.

## Candidate: StatsBomb Open Data

Source repository: `hudl/open-data` / StatsBomb Open Data.

Useful facts:
- provides match JSON, events and lineups for selected open competitions.

R39P rejection:
- published open-data structure describes match/event/lineup content, but this screen found no original pre-match lineup publication/observation timestamp attached to each historical lineup;
- therefore it cannot establish when the lineup became known before kickoff.

Decision: **REJECT for observed-at lineup research**. Retain only as post-event/open analytical data.

## Candidate: openfootball / worldcup.json

Useful facts:
- public/no-key football data and lineup/detail projects exist.

R39P rejection:
- project documentation explicitly describes manual/wiki-style updates rather than guaranteed live observation logging;
- lineup/detail history is not an append-only pre-kickoff observed-at archive.

Decision: **REJECT for observed-at historical validation**.

## Candidate: public injury-history datasets derived from Transfermarkt

Useful facts:
- open processed injury datasets exist, including EPL player-day/injury-history research assets.

R39P rejection:
- these are based on injury spell/history records, not an archive of the original publication time at which the injury information became public pre-match;
- this is the same identifiability problem already encountered in earlier project injury-onset work.

Decision: **REJECT as authentic pre-match observed-at evidence**.

## Existing project trajectory data

Beat-The-Bookie/market trajectory assets already provide genuine time-indexed market movement and were explored in R39C-R39F. They are not a new R39P information family and must not be recycled as a nominally new experiment.

## Terminal screen conclusion

No source in this screen supplies a sufficiently large, already-existing, public/no-credential archive of **original pre-match observation timestamps for lineup/injury information** that can legitimately replace a forward collector.

Therefore R39P remains:

`STOP_R39P_NO_EXISTING_AUTHENTIC_FORWARD_SAMPLE_WITHOUT_NEW_PROVIDER_COLLECTION`

Next admissible choices remain:

1. discover a genuinely different public archive that satisfies the full original-timestamp contract; or
2. with separate user authorization, restart genuine forward collection and wait for enough observations before fitting any challenger.

Nothing in this file authorizes API keys, provider requests, scheduled collection, training, formal-weight change, or blind-label access.
