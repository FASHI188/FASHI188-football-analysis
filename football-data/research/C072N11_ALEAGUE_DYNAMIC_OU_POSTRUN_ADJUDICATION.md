# C072-N11 — A-League dynamic multi-line O/U zero-label source adjudication

Project: football3 only
Parent: C072-N10 static closing five-line P(T), terminal PARK
PR: #297

Official source-audit run: `32251618515`
Official job: `96063712069`
Artifact: `football3-c072n11-aleague-dynamic-ou-zero-label`
Artifact id: `9364629795`
Artifact digest: `sha256:711f9bc92c9314f0deaa881ca3228428b8a6cc08e1e2d68c180e912b95cfb248`
Source repo: `betfair-datascientists/betfair-datascientists.github.io`
Source revision: `9fe7fb127cd05316dbd438fe0e5be82c5c3ed536`

## Terminal
`ALEAGUE_DYNAMIC_OU_SOURCE_PASS`

This is a zero-label source PASS only. It is not a scientific effect PASS and authorizes no target access by itself.

## Exact pooled coverage
- allowed-field-only rows decoded: 239,002
- eligible preferred O/U runner rows: 9,992
- recognized preferred O/U market instances: 4,996
- fail-closed validated event-line instances: 4,926
- unique non-conflict A-League match identities with preferred O/U: 992
- O/U2.5 complete at all provider-native T-60/T-30/T-1 snapshots: 962
- matches with >=2 preferred lines complete at all three snapshots: 983
- matches with >=3 preferred lines complete at all three snapshots: 979
- matches with all five preferred lines complete at all three snapshots: 944

Pooled line all-three completeness:
- 0.5: 974
- 1.5: 974
- 2.5: 962
- 3.5: 972
- 4.5: 977

## Per-season >=3-line all-three completeness
- 2020-2021: 149
- 2021-2022: 163
- 2022-2023: 161
- 2023-2024: 168
- 2024-2025: 176
- 2025-2026: 162

Per-season all-five all-three completeness:
- 2020-2021: 125
- 2021-2022: 156
- 2022-2023: 159
- 2023-2024: 168
- 2024-2025: 174
- 2025-2026: 162

## Diagnostics
- identity-conflict events detected and fail-closed excluded: 2
- event-lines with multiple market ids and fail-closed excluded: 30
- crossed runner snapshots observed: T-60=4, T-30=9, T-1=76; these snapshots are excluded from completeness
- all six exact source SHA256 values matched the header-only freeze

## Boundary audit
- forbidden outcome/result field values accessed: 0
- target/outcome values materialized: 0
- model fits: 0
- model scores: 0
- C073-C077 scientific results used: false
- C070-F Confirmation1597 opened: false

## Global-consumption audit status before target access
Searches performed in the shared GitHub/Airtable history found no prior experiment on the exact Betfair Data Scientists A-League All Markets files and no prior `A-League_2025-2026` record. Repository history does contain a metadata registry reference to ten 2024/25 SkillCorner A-League sample matches, but no PR experiment using those samples was found. This is recorded as a potential identity-level overlap to treat conservatively if exact identities become available; it does not establish consumption of the 2025/26 Betfair A-League pool.

No target field from the six Betfair files was read during this audit.

## Scientific continuation ruling
The source scale and PIT semantics are sufficient to preregister a separate C072-N12 P(T) experiment. N12 must be frozen before reading `TOTAL_GOALS` or any other result field.

The clean incremental question is dynamic single-line versus dynamic multi-line market information:
- strong baseline must contain the O/U2.5 trajectory itself, not a weak static baseline;
- candidate may add the other preferred line trajectories under the same model and paired rows;
- matched volume is excluded from the first effect test so the estimand remains probability-surface information rather than liquidity;
- 2025/26 must remain unopened until the development gate and a one-shot confirmation boundary are frozen.
