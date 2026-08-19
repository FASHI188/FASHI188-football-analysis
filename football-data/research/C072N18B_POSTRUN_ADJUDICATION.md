# C072-N18B — zero-label target/market/xG-state join postrun adjudication

## Terminal
`STOP_COVERAGE`

This is a **zero-label coverage stop**, not a scientific model failure.

## Authority
- project: `football3`
- parent: N18A2 postrun `0327b9eee154206cd74b9fadf146445969c846fb`
- branch: `football3/c072n18b-zero-label-target-market-join-20260820`
- effective HEAD: `63f57eb2cc25eb8546b546d87927ded964ee9f0a`
- PR: #314
- authoritative run: `32276979117`
- job: `96146597483`
- artifact: `9374435074`
- artifact digest: `sha256:40ad90269157c72d695e04134a985766103e3718755564d1b0f62877a6bae0ed`

## Prior technical run
Run `32276620531` stopped `FOOTIQO_TABLE_PROTOCOL EPL` before target odds rows were materialized. Implementation correction 01 changed only table disambiguation to the previously verified N16R1 exact-schema rule; the scientific/coverage contract was unchanged.

## Zero-label coverage result
Frozen target window: 2024-09-18 through 2024-12-31.

Footiqo odds-only rows inside the frozen window: **738**.

Per source:
- EPL: 148
- LaLiga: 131
- Bundesliga: 107
- Serie A: 138
- Ligue 1: 100
- MLS: 114

Historical FotMob source:
- source matches reconstructed: 9,021
- usable historical matches under frozen gate: 9,014

After the frozen exact-name mapping, >=8 prior-match history gate and O/U2.5 gate:
- eligible rows: **452**
- required rows: **550**
- deficit: **98**

Frozen ineligibility reasons:
- team_mapping: **249**
- history_lt8: **37**

Source-target normalized identity overlap: **0**.

## Boundary
- target result columns requested/materialized: 0
- target result values materialized: 0
- model fits: 0
- target scores: 0
- C070-F Confirmation1597 opened: false
- sealed reserves opened: false
- C073-C077 scientific results used: false

Thus no N18 target outcome evidence has been consumed.

## Adjudication
N18B itself is permanently `STOP_COVERAGE`. Do not extend its date window, reduce the 550 requirement, lower the >=8-history gate, change the 400/150 split, add leagues, or alter the frozen exact-name normalizer and then relabel the rerun as N18B PASS.

The dominant blocker is identity resolution rather than market coverage: 249/738 zero-label target rows fail the exact cross-provider team-name map. Because no target outcome has been opened, a **separate zero-label N18B2 hypothesis** may test a preregistered, high-confidence automated identity resolver. It must preserve the same six leagues, date window, 550 requirement, >=8 history gate, fixed 16 features, O/U2.5 market anchor and 400/150 chronological split. No manual aliases or outcome-assisted mapping are permitted.
