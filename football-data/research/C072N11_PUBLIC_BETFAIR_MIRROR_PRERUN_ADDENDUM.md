# C072-N11 public Betfair mirror — pre-run structural replay addendum

Project: football3 only
Parent contract: `C072N11_DYNAMIC_MULTILINE_OU_ZERO_LABEL_SOURCE_CONTRACT.md`
Frozen before this mirror is executed.

## Purpose
Audit the public `marcosf63/bet` Betfair BASIC mirror at immutable revision `90f818e2ad78aa3c624a0fe251c3e60fcfb0ccff` for real timestamped O/U stream structure and T-24h/T-6h/T-1h coverage without reading any football outcome or settlement field.

## Scientific classification
The same pinned repository/domain was previously scanned by another quarantined project and H/D/A winner information was materialized for MATCH_ODDS events. Therefore this mirror is **GLOBALLY CONSUMED at the outcome-domain level**. Any later target scoring on these matches would be REPLICATION / REPRODUCTION only.

This audit may establish engineering/source structure, but it can never by itself establish fresh evidence, independent confirmation, or a football3 scientific breakthrough.

## Safe parser contract
The audit may access only:
- top-level `pt` publish time;
- `mc[].id` market id;
- `marketDefinition.eventId`;
- `marketDefinition.marketType`;
- `marketDefinition.marketTime` or `openDate`;
- `marketDefinition.inPlay` only to reject in-play states;
- `rc[].id` selection id;
- `rc[].ltp` last traded price.

The audit MUST NOT access or materialize:
- runner `status` / `WINNER` / settlement;
- score, goals, result, H/D/A target;
- any post-kickoff feature;
- C070-F Confirmation1597 or any protected sample.

No runner-name assumption is needed for source coverage. For each O/U market at a cutoff, a quote is structurally complete only if at least two distinct selection ids have valid finite LTP > 1 observed at or before the cutoff and before kickoff.

## Frozen market mapping
Recognized market types are exactly `OVER_UNDER_05`, `OVER_UNDER_15`, `OVER_UNDER_25`, `OVER_UNDER_35`, `OVER_UNDER_45` for the preferred five-line surface. Other `OVER_UNDER_*` market types may be counted diagnostically but cannot substitute for a missing preferred line.

## Frozen cutoffs
T-24h, T-6h, T-1h. At cutoff c use only the latest observation whose publish time is `<= kickoff - c`.

## Structural replay reporting
Report, without targets:
- total files and recognized O/U markets;
- unique event ids with O/U;
- per-line event counts;
- per-line complete two-runner coverage at each frozen cutoff;
- O/U2.5 complete at all three cutoffs;
- events with >=2, >=3, and all 5 preferred lines complete at all three cutoffs;
- publish-time and kickoff ranges;
- parser errors;
- explicit counters proving target/settlement fields accessed = 0 and model fits = 0.

## Ruling
`STRUCTURAL_REPLAY_PASS` means only that the consumed public mirror contains technically usable dynamic O/U structure. It does not authorize target access.
`STRUCTURAL_REPLAY_STOP` means the public mirror cannot carry N11 further.

Because the mirror is globally consumed, **full N11 source PASS still requires a separate unconsumed provider export/domain** with the same frozen PIT semantics before any fresh scientific scoring plan can be opened.
