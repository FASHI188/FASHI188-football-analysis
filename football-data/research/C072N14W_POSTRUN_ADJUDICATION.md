# C072-N14W — Post-run adjudication

## Binding terminal
`C072N14W_ALEAGUE_WOMEN_SURFACE_ZERO_LABEL_STOP`

Classification: ZERO_LABEL_SOURCE_COVERAGE only. No target/result values were materialized and no model was fit/scored.

Authoritative run:
- workflow run `32258210499`
- job `96084852466`
- artifact `football3-c072n14w-aleague-women-surface-zero-label`
- artifact id `9367157252`
- artifact ZIP SHA256 `7a121127233abc157536a350977b8de70ddb07df9eb99285132364239236bc34`

## Frozen source facts
Pinned repository/revision:
`betfair-datascientists/betfair-datascientists.github.io@9fe7fb127cd05316dbd438fe0e5be82c5c3ed536`

Per-season preferred-O/U events / all-five-all-three events / file SHA256:
- 2020-2021: 57 / **36** / `2c33eea38183be5684acb00c67de5379099563f939f3d5fa8336699c1ee01462`
- 2021-2022: 74 / **59** / `f2e2a957c7674c5626eff71c7ab360e698c1ccb9f8054383e5c5c0eb6498a86f`
- 2022-2023: 103 / **61** / `899992d57d65c4786b47057a8c009dbeea3ec170aadc29beb1fd47e4b12a7524`
- 2023-2024: 139 / **139** / `85e3b6d3cf2f3e54e1adbe456e24ec5042946e5102c99501c412039279814792`
- 2024-2025: 145 / **135** / `3daf737dd36ef95b4e2e0d354674842faec755016e43abb9f987d67b1d594060`
- 2025-2026: 117 / **116** / `2c0c85bf0856b2e13de7a862a6a1e83ccd6aa0781d3aa2058ee289433f4c2053`

Pooled:
- preferred-O/U events: 635
- O/U2.5 all-three: 622 (97.95%)
- all-five/all-three: 546 (85.98%)
- identity conflicts: 0.

## Gate adjudication
Every N14W gate passed except:
`each_dev_season_all5_all3_ge_45 = false`

The failure is solely 2020-2021, where all-five/all-three count is 36 versus the frozen minimum 45. N14W therefore remains STOP permanently; its threshold must not be lowered and the run must not be relabeled PASS.

## Zero-label continuation allowed
Because no target labels were opened, a separately frozen new zero-label data plan may exclude the under-covered 2020-2021 season rather than lower N14W's gate.

A legitimate successor may preregister:
- development domain: 2021-2022 through 2024-2025, fixed all-five/all-three zero-label inventory 59+61+139+135 = **394**;
- sealed reserve: 2025-2026, fixed zero-label inventory **116**;
- 2020-2021: excluded from that successor, not repaired or substituted.

This is a new data plan, not a retroactive N14W PASS.

## Hard boundaries
- target/result values read/materialized in N14W = 0;
- model fit/score = 0;
- C073-C077 scientific results used = false;
- C070-F Confirmation1597/protected remain sealed;
- formal_weight=0.
