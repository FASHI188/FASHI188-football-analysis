# C072-N18A1 FotMob static-release discovery — postrun

## Verdict
`PASS_STATIC_RELEASE_METADATA_DISCOVERY`

Zero-label / metadata-only source gate passed.

## Execution evidence
- branch: `football3/c072n18a1-fotmob-static-shotxg-discovery-20260819`
- execution HEAD: `694b9beb22d7a9551a8fcad65a64d311e1bdcf7c`
- run: `32275378990`
- job: `96141454323`
- artifact: `9373788423`
- artifact digest: `sha256:a7e8af77dd1eed579c0726067b48006a1f34cf4ceb40b2cfe84b01951b231ef3`
- release id: `79989708`
- release tag: `fotmob_match_details`
- published: `2022-10-15T14:20:26Z`
- declared release bytes: `241024425`

## Exact assets discovered
9 CSV match-detail assets (plus matching RDS copies):
- `42_match_details.csv`
- `47_match_details.csv`
- `50_match_details.csv`
- `53_match_details.csv`
- `54_match_details.csv`
- `55_match_details.csv`
- `73_match_details.csv`
- `87_match_details.csv`
- `130_match_details.csv`

League mapping from the upstream FotMob league catalogue identifies the five primary domestic assets needed for the next acquisition:
- 47 = England Premier League
- 87 = Spain LaLiga
- 54 = Germany 1. Bundesliga
- 55 = Italy Serie A
- 53 = France Ligue 1

Other discovered CSVs are international/MLS assets and are not needed for the first 5,000-match history-source attempt.

## Boundary
- match-detail data bytes downloaded: 0
- target labels accessed: 0
- model fits: 0
- target scores: 0
- sealed reserve / C070-F access: 0
- C073-C077 scientific use: 0

## Next
Freeze a separate acquisition contract on exactly CSV assets `47/53/54/55/87` before downloading them. Selection must be deterministic from match identity/date only and retain exactly 5,000 usable shot-xG matches if coverage permits; no result-based replacement or model-dependent selection is allowed.
