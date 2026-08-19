# C072-D — Betfair BASIC dynamic O/U zero-label source contract

## Lineage / isolation
- Project line: **football3** only.
- Parent scientific checkpoint: C072-C, HEAD `e3e73c998020beef585cc459a69ea5b73b44ddb3`.
- C073 through C077 are explicitly quarantined and are **not** evidence, state, ancestry, tuning guidance, or stopping-rule input for this experiment.
- formal_weight = 0. No CURRENT change.

## Scientific question
Can auditable, timestamped pre-match football Over/Under market trajectories provide a genuinely orthogonal match-level information axis for improving complete `P(T=0..7+)` resolution beyond the frozen C072-C score-history baseline?

This stage is **SOURCE / COVERAGE AUDIT ONLY**. It must not read football result labels, fit a P(T) model, score predictions, tune thresholds, or open any sealed confirmation set.

## Pinned provider semantics
Provider: Betfair Exchange Historical Data, BASIC tier.

Required semantics before any downstream modelling:
1. Historical exchange messages carry original publish time (`pt`) and market definitions.
2. BASIC data are approximately one-minute snapshots / updates of last traded price and do not provide the full price ladder or traded-volume depth.
3. Soccer supports multiple Over/Under goal markets (for example 2.5, 3.5, 5.5, 6.5; the parser must discover available lines from market names rather than hard-code a single line).
4. Only observations strictly before market kickoff may be retained for prematch features.

Provider documentation references (provenance only):
- https://support.developer.betfair.com/hc/en-us/articles/360002407732-What-data-is-provided-by-the-Historical-Data-service
- https://support.developer.betfair.com/hc/en-us/articles/25918481395740-How-is-the-BASIC-Historical-Data-traded-volume-updated
- https://betfair-developer-docs.atlassian.net/wiki/spaces/1smk3cen4v3lu3yomq5qye0ni/pages/2687786/Getting+Started
- https://betfair-datascientists.github.io/data/usingHistoricDataSite/

## Allowed fields during source gate
May parse only market-stream fields needed to identify and timestamp prematch O/U observations:
- top-level publish timestamp `pt`
- market id
- event id / event name
- market name / market type
- market kickoff / open time
- in-play flag
- runner id / runner name
- last traded price (`ltp`)

No football target result, score, total-goal label, winner/settlement label, or post-match performance field may be joined or materialized.

## PIT rule
For every retained quote/update:
`publish_time < kickoff_time` must hold.

Any message at or after kickoff is excluded from the source audit. No later message may backfill an earlier timestamp.

## O/U line parsing
Discover half-goal lines mechanically from market names matching `Over/Under X.5 Goals` (case-insensitive). Do not assume only O/U2.5 exists. Runner orientation must be resolved from runner names containing `Over` / `Under`, never from runner array position alone.

## Frozen zero-label coverage gates
The source gate may PASS only if all are true on the downloaded BASIC football package:
- parsed unique football events >= 3,000
- duplicate event identity conflicts = 0
- valid kickoff timestamp rate >= 99.5%
- O/U2.5 present prematch for >= 85% of parsed events
- at least 2 distinct half-goal O/U lines present prematch for >= 75% of parsed events
- at least 3 distinct half-goal O/U lines present prematch for >= 50% of parsed events
- among events with O/U2.5, at least one valid prematch LTP observation exists >= 60 minutes before kickoff for >= 70%
- among events with O/U2.5, at least one valid prematch LTP observation exists within the final 60 minutes before kickoff for >= 70%
- retained observations with `publish_time >= kickoff_time` = 0
- football target labels materialized = 0
- model_fit = 0

If any gate fails, terminal status is `STOP_SOURCE_COVERAGE`; do not relax gates on the same downloaded package and relabel it as a PASS.

## Downstream experiment if source gate passes
A separate preregistered C072-E contract must be frozen **after** this zero-label source gate and **before** any target outcomes are opened. The downstream candidate may use only frozen summaries of the prematch multi-line O/U trajectory; no feature search may occur on scored target labels.

Candidate design is intentionally not frozen here because source availability (line count and timestamp density) must first be measured without outcomes.

## Hard boundaries
- C072-C viewed 959 labels cannot be used to search weights, windows, transforms, or line subsets.
- C073-C077 results are quarantined and cannot be used to choose this contract or interpret its outcome.
- C070-F Confirmation1597 remains sealed.
- No protected sample is opened.
- No exact-score matrix, Draw boost, 1-1 boost, or formal promotion is allowed in this stage.
