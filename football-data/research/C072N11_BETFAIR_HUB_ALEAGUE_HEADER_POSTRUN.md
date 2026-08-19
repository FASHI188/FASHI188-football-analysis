# C072-N11 Betfair Hub A-League — header-only post-run adjudication

Project: football3 only
Official run: 32251165778
Official job: 96062261154
Artifact: `football3-c072n11-aleague-header-only`
Artifact id: 9364455613
Artifact digest: `sha256:3bc7a44555cbab02ae35cf829379a209228c83413236c8d39870e5178acf733c`
Source repo: `betfair-datascientists/betfair-datascientists.github.io`
Frozen source revision: `9fe7fb127cd05316dbd438fe0e5be82c5c3ed536`

## Terminal
`ALEAGUE_HEADER_PASS`

## Boundary result
- decoded data rows: 0
- outcome values materialized: 0
- model fits: 0

## Six pinned files and SHA256
- 2020-2021: 10,573,139 bytes; `e794f97ce8d95676a0cf14a78057aba8837973459eda2a1e04194402c4bfaa37`
- 2021-2022: 10,847,589 bytes; `5411a5a311a2f2e379967b585a2e54646168ca434923e4ca5389cb614d27de78`
- 2022-2023: 10,255,952 bytes; `916724fe4cad4af6d350805f4962c94456a14edc7afcc6184b01f3fcb77fc06d`
- 2023-2024: 11,034,350 bytes; `2e4f761f484891bec3457b4c52a3d1ee20e5379fe5efa69e6564133edcbec1b7`
- 2024-2025: 11,314,326 bytes; `a62ce23b14c112ae02be470bf8e29f3568f2a40a311b2b0034be6cd8c1b53cb3`
- 2025-2026: 10,641,321 bytes; `f0980a3a37b79a5be947e4a7e3288c9f88e3cf03810b4b23cb8f8871064fc5ab`

## Frozen observed header
All six files expose the same 27 columns:
`EVENT_DATE, PATH, EVENT_ID, MARKET_TYPE, MARKET_ID, MARKET_NAME, SELECTION_ID, RUNNER_NAME, HANDICAP, RUNNER_STATUS, IS_WINNER, HOME_TEAM, AWAY_TEAM, TOTAL_GOALS, HOME_SCORE, AWAY_SCORE, BEST_BACK_PRICE_60_MIN_PRIOR, BEST_LAY_PRICE_60_MIN_PRIOR, MATCHED_VOLUME_60_MIN_PRIOR, BEST_BACK_PRICE_30_MIN_PRIOR, BEST_LAY_PRICE_30_MIN_PRIOR, MATCHED_VOLUME_30_MIN_PRIOR, BEST_BACK_PRICE_1_MIN_PRIOR, BEST_LAY_PRICE_1_MIN_PRIOR, MATCHED_VOLUME_1_MIN_PRIOR, TOTAL_MATCHED_VOLUME, LAST_PREPLAY_PRICE`.

## Interpretation
This is materially stronger source structure than the legacy one-week zygmunt sample for the N11 question because the official public CSV schema directly exposes fixed prematch snapshots at 60, 30 and 1 minutes prior. No row-level claim about O/U coverage has yet been made.

Outcome/settlement columns are present in the files but remain forbidden until a separately frozen scientific target-access contract. The next legal step is a zero-label row-level coverage audit that reads only identity, market and prematch quote fields.
