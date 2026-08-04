# R34 Policy 2024/25 — Input Freeze Addendum V1

## Status

- Research-only isolated pure 1X2 track.
- `formal_weight=0`.
- No model scoring had been performed when this addendum was written.
- The structural preregistration in `R34_POLICY_2425_PREREG_V1.md` remains unchanged.

## Recovered market provenance

The original R34.0 code referenced the extracted GitHub Actions artifact directory `gold1000_artifact_8843568061`. Artifact ID `8843568061` was recovered before the cross-season policy run.

The exact files and SHA-256 values recovered from that artifact are:

- `raw/ENG_PremierLeague_2324_E0.csv`
  - bytes: `172196`
  - SHA-256: `b2e057b0ed959f198b0f63d2391c01239f3608e6de5db68edab3f88e04d07ff3`
  - rows: `380`
- `raw/ENG_PremierLeague_2425_E0.csv`
  - bytes: `197110`
  - SHA-256: `d0c8ce4a96d886cf60cf101f570f4a3893844226f91c7bd769eb568c49edbfa4`
  - rows: `380`

Both contain the frozen closing-market columns used by R34.0: `AvgCH`, `AvgCD`, `AvgCA`, `AvgC>2.5`, and `AvgC<2.5`.

## Correction of stale hash metadata

A prior continuation summary recorded the 2023/24 market SHA-256 as `760f6881175fba2ebccfb89c4a07acbd4172262daee6d07f3baf5dc379242333`. Direct recovery of the exact referenced Artifact ID demonstrates that this metadata value does not identify the R34.0 input file. It is therefore rejected as stale or misattributed metadata and is not used.

The recovered Artifact bytes are the controlling evidence for the market inputs. The public historical URLs are permitted only as transport mirrors and must fail closed unless their bytes match the recovered hashes above.

## Other frozen source

The 2023/24 goal-time source remains pinned to:

- repository: `schochastics/football-data`
- commit: `6ba5e7e8f8657b6ccdeb0e89778765423f8d5aaf`
- path: `data/goals_time/eng-premier-league.csv`
- expected Git blob SHA-1: `9b7d9c4428ab16b509c7de55eaf4c5f9720ff42a`

## Prohibitions

This addendum does not authorize parameter changes, feature changes, threshold selection, policy-label-driven filtering, promotion, production writes, or any change to R20, R23, formal model, formal data, configuration, or CURRENT.
