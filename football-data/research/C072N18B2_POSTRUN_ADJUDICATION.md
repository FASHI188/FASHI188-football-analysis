# C072-N18B2 — zero-label target/market/xG-state join postrun

## Verdict
`PASS_N18B2_ZERO_LABEL_TARGET_MARKET_JOIN`

This is a **zero-label cohort construction PASS**, not a scientific model-effect PASS.

## Authority
- parent N18B: `STOP_COVERAGE` @ `364b5560556d92e97916e6dbc56eb1d0552913a8`
- branch: `football3/c072n18b2-zero-label-unique-fuzzy-team-map-20260820`
- execution HEAD: `7b6c5e79384eb870fd734e6764191ac2ab56f68d`
- PR: #316 Draft/Open at execution
- run: `32277577175`
- job: `96148491617`
- artifact: `9374649816`
- artifact digest: `sha256:e121064741883f0a7a6c202d729f9da1390a97f3de5327a14b1dd7b5ec96462e`

## Frozen identity resolver result
N18B2 added exactly one preregistered zero-label resolver on top of N18B:
- `rapidfuzz.fuzz.token_set_ratio`
- same league asset only
- shared normalized token required
- best score >= 90
- unique best
- best-minus-second margin >= 10
- no manual aliases.

Accepted fuzzy mappings: **16 unique target team names**.
Mapping receipt rows: 125.
Mapping receipt SHA256: `10ead4e81f438e6d8409d14a8d24553f031e0408dbb1131573d8b063d70657f5`.

## Coverage
Frozen Footiqo target-window odds-only rows: **738**.

N18B2 eligible rows after all unchanged gates: **611**.
Ineligible:
- team_mapping: 66
- history_lt8: 61

Source-target identity overlap: **0**.

Chronological cohort frozen exactly:
- selected: **550**
- DEVELOPMENT: **400**
- CONFIRMATION_SEALED: **150**

Selected league counts:
- Bundesliga 67
- EPL 93
- LaLiga 84
- Ligue1 88
- MLS 114
- Serie A 104

Target range: 2024-09-18T19:00:00 through 2024-12-15T19:30:00.

## Immutable cohort hashes
- target550 zero-label payload: `sha256:bd41147c39239f1c2c7ab1e5f8100d6bf143203e574a4f6ffeb839762ab29906`
- dev400 IDs: `sha256:55181a078d39d9ac53881aa0c377d6c6cb819c06053bd75609841a13caa1dbdf`
- confirmation150 IDs: `sha256:774be269e30254af29614210401b52c23b0f3a4e79a7945e98014d50590ea90f`
- original team mapping receipt: `sha256:79e77d8e577dce172f158428c11c11dbbefe65d88187f1c8f52d049136492894`

## Boundary
- target result columns requested/materialized: 0
- target result values materialized: 0
- model fit: 0
- target score: 0
- C070-F Confirmation1597: unopened
- existing sealed reserves: unopened
- C073-C077 scientific results: unused

Therefore both dev400 and confirmation150 outcome labels remain unopened at this checkpoint.

## Next legal step
Freeze C072-N18C before any dev outcome query. N18C may open only the dev400 target outcomes under a target-only transport; the confirmation150 IDs must be excluded at the request layer and remain sealed unless the complete frozen development gate passes.
