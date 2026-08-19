# C072-N11 zygmunt/betfair-sports — original zero-label audit adjudication

Official run: 32249987027
Official job: 96058615387
Artifact: `football3-c072n11-zygmunt-ou-zero-label`
Artifact id: 9364038361
Artifact digest: `sha256:ef705a4594d857b0fe283220f4c4e2c05252ac2a97d40b25106cfcd105f86609`
Source file: `betfair_140901.csv`
Source file bytes: 337,478,465
Source SHA256: `ce72ba2ebdc79bf22b169f32fa279a4adee7ef5b6b946ae87e2a39decb291fb4`

## Original terminal
`ZYGMUNT_OU_SOURCE_LIMITED`

The original audit is immutable and is not retroactively changed.

## Key zero-label findings
- 1,306,748 rows scanned
- 857,936 Soccer rows
- 190,834 pre-event Soccer rows
- 66,494 candidate total-goal rows
- 4,565 clean candidate O/U event-line markets
- exact market names include Over/Under 0.5, 1.5, 2.5, 3.5, 4.5 Goals and additional higher lines
- O/U2.5 latest-completed-level proxy coverage: T-24h 44, T-6h 174, T-1h 420
- O/U2.5 strict-identifiable coverage: T-24h 13, T-6h 25, T-1h 56
- forbidden outcome/settlement values materialized: 0
- model_fit: 0

## Engineering issue discovered without labels
The source field called `EVENT_ID` does not yield cross-line match grouping in this export: grouping by it produced zero matches with >=2 preferred O/U lines despite thousands of line markets. This is a source-identity issue, not a scientific result.

A versioned R1 zero-label correction may reconstruct match identity only from allowed source metadata (`FULL_DESCRIPTION`, `SCHEDULED_OFF`) and must narrow market parsing to exact full-match `Over/Under X.5 Goals` names. No target field may be opened.
