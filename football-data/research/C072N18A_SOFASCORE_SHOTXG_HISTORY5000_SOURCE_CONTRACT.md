# C072-N18A — SofaScore shot-xG history-5000 source contract

## Status
- project: `football3`
- parent: C072-N18 route HEAD `71b345c78d3dda934918a86cbe090e27a52a2528`
- branch: `football3/c072n18a-sofascore-shotxg-history5000-20260819`
- purpose: **ZERO-TARGET source acquisition only**
- requested size: exactly **5,000 historical matches** with usable shot-level xG
- no model fit, no target scoring, no scientific PASS claim

## Why this source
N18 requires materially new non-market prematch information that can describe match-level chance generation and shot-quality distribution beyond the total-goal market anchor.

Global-consumption audit before this contract found extensive prior Understat use in the shared repository and Airtable history, so Understat cannot be relabeled fresh. The repository search found no prior SofaScore shotmap/xG pipeline and the shared Airtable maintenance log returned no SofaScore research record. SofaScore is therefore admitted only as a **new-source candidate**, subject to this one-shot coverage acquisition.

## Source
Public SofaScore HTTP JSON endpoints under `https://www.sofascore.com/api/v1`:
- `/unique-tournament/{id}/seasons`
- `/unique-tournament/{id}/season/{season_id}/events/last/{page}`
- `/event/{event_id}/shotmap`

The source is a live public service rather than an immutable archive revision. Therefore this run MUST record:
- exact UTC collection time,
- selected event identities,
- deterministic selection seed/rule,
- output SHA-256 digests,
- tournament/season inventory,
- request failure counts.

A later scientific experiment may not call this a source-revision-level pristine confirmation unless a separately immutable snapshot is frozen first.

## Frozen competition and season scope
Candidate tournaments, in this order only for inventory construction:
1. Premier League — unique tournament 17
2. LaLiga — 8
3. Bundesliga — 35
4. Serie A — 23
5. Ligue 1 — 34
6. Eredivisie — 37
7. Liga Portugal — 238

Eligible season labels: `21/22`, `22/23`, `23/24`, `24/25` only. `25/26` and `26/27` are excluded from this history-source acquisition.

## Match eligibility and deterministic selection
1. collect finished-event identities from the frozen tournament/season scope;
2. persist only identity/time/team/tournament/season metadata from event listings — never score, winner code, result or standings fields;
3. rank candidates by SHA-256 of `C072N18A_HISTORY5000|event_id` (tie-break event_id ascending);
4. fetch shotmap in that fixed order;
5. a match is usable when it has at least 6 numeric shot-xG observations and at least one numeric shot-xG observation for each side;
6. retain the first exactly 5,000 usable matches;
7. no replacement based on any score/result/model metric is permitted;
8. if fewer than 5,000 usable matches exist, terminate `STOP_COVERAGE` and do not lower the gate.

## Persisted shot fields
Only chance-state fields needed for later strictly historical feature construction may be persisted:
- event_id
- is_home
- xg
- situation
- body_part
- shot_time / added_time when present
- player_coordinates x/y when present

Explicitly forbidden from persisted outputs:
- homeScore / awayScore
- winnerCode
- result labels
- standings
- shot outcome / goal flag / shotType outcome
- any derived target T/HDA/Draw label

The upstream event-list and shotmap responses may technically contain outcome information in process memory. For governance, **every retained history5000 identity is therefore globally consumed as a target identity** and may never later be presented as pristine development/confirmation target evidence. Its allowed role is historical feature source only.

## Required outputs
Artifact must contain:
- `sofascore_n18a_history5000_matches.jsonl.gz`
- `sofascore_n18a_history5000_shots.jsonl.gz`
- `sofascore_n18a_history5000_summary.json`

Summary must include selected match count, shot count, xG coverage, competition/season counts, first/last dates, source URLs, collection timestamp, request failures, and SHA-256 for both gzipped data files.

## Scientific boundary
This acquisition does **not** authorize:
- target-result join,
- B0/C model construction,
- latent-family selection,
- market-anchor tuning,
- target scoring,
- opening any sealed reserve or C070-F Confirmation1597,
- using C073-C077 scientific conclusions.

After a 5,000-match acquisition PASS, the only legal next step is N18-B: freeze an immutable feature-snapshot identity plus a separate target cohort and market-anchor join before any target outcome access.
