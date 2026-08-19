# C072-N11 — zygmunt/betfair-sports zero-label O/U source addendum

Project: football3 only
Parent: `C072N11_DYNAMIC_MULTILINE_OU_ZERO_LABEL_SOURCE_CONTRACT.md`
Frozen before any row-level source inspection by football3.

## Source
Kaggle public dataset: `zygmunt/betfair-sports` — One week of Betfair data: 23 sports.

Shared-repository history records a prior no-label Match Odds timestamp-coverage audit of this source. That prior audit forbade WIN_FLAG and score/result access. No target-label consumption of this exact source has been established. This addendum is independent source metadata/coverage work only and does not inherit any quarantined model result.

## Question
Does the source contain Soccer Over/Under goal markets with enough pre-kickoff timestamp structure to support a later dynamic P(T) source gate?

## Allowed row fields
Only fields needed for source identity, market identity, timestamp coverage, and quoted odds may be materialized:
- SPORTS_ID
- EVENT_ID
- FULL_DESCRIPTION
- SCHEDULED_OFF
- EVENT
- SELECTION
- ODDS
- FIRST_TAKEN
- LATEST_TAKEN
- IN_PLAY
- SELECTION_ID
- VOLUME_MATCHED

Column-name matching is case-insensitive. Missing optional allowed fields may be reported.

## Forbidden fields
Values from all outcome/settlement fields are forbidden, including but not limited to:
- WIN_FLAG
- SETTLED_DATE
- winner/result/score/goal labels

They must not be emitted, aggregated, counted by value, joined, scored, or used for selection. If a forbidden column exists, only its header name may be noted; its row values remain unavailable to this experiment.

## Market discovery rule
Within Soccer only, identify O/U candidates mechanically from EVENT/FULL_DESCRIPTION/SELECTION text containing goal-total semantics such as `over`, `under`, `goals`, or explicit total lines. Do not inspect WIN_FLAG to identify markets.

Preferred surface lines are 0.5, 1.5, 2.5, 3.5, 4.5. Report other half-goal lines separately.

## Time interpretation
The source aggregates price levels with FIRST_TAKEN and LATEST_TAKEN rather than exact Stream messages. Therefore:
- exact LTP snapshot claims are forbidden;
- each price level provides an observed activity interval only;
- at T-24h/T-6h/T-1h report whether a line/selection has price-level activity that is temporally identifiable at/before the cutoff;
- all retained times must be strictly before SCHEDULED_OFF;
- IN_PLAY observations are forbidden for prematch coverage.

## Zero-label outputs
Report:
- source file identities and hashes;
- row/column schema without forbidden-field values;
- Soccer row count based only on SPORTS_ID/description fields;
- candidate O/U market/event counts;
- discovered total lines;
- per-line event coverage;
- T-24h/T-6h/T-1h temporal coverage;
- events with >=2, >=3, and all five preferred lines;
- source timestamp range;
- no-label counters proving outcome/settlement values materialized = 0;
- model_fit = 0.

## Source ruling
`ZYGMUNT_OU_SOURCE_PASS` only if real Soccer O/U market rows exist and at least O/U2.5 has identifiable prematch observations at >=2 frozen time cutoffs for >=100 unique events.

`ZYGMUNT_OU_SOURCE_LIMITED` if O/U exists but the 100-event dynamic gate is not met.

`ZYGMUNT_OU_SOURCE_STOP` if no genuine Soccer O/U market family is found or timestamp semantics cannot support dynamic prematch use.

No ruling authorizes target access. A later modeling contract must be frozen separately and must re-audit global match consumption before any label is read.
