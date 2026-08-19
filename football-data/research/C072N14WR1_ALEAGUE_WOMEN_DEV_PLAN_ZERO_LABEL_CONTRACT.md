# C072-N14WR1 — A-League Women development-plan zero-label adjudication

## Status / lineage
- Project: football3 only.
- Parent N14W remains permanently `C072N14W_ALEAGUE_WOMEN_SURFACE_ZERO_LABEL_STOP`.
- N14W authoritative zero-label run: `32258210499`, artifact `football3-c072n14w-aleague-women-surface-zero-label`, artifact id `9367157252`, ZIP SHA256 `7a121127233abc157536a350977b8de70ddb07df9eb99285132364239236bc34`.
- N14WR1 must use that exact immutable zero-label summary only. It may not refetch source rows, inspect target fields, fit a model, or alter any N14W gate.

## Purpose
Freeze a **new data plan** after the zero-label N14W source STOP without retroactively changing N14W.

The sole N14W failure was 2020-2021 all-five/all-three coverage = 36 < frozen 45. No target labels were opened. N14WR1 therefore excludes 2020-2021 from the new scientific plan instead of lowering the gate.

## Immutable planned domains
Development seasons:
- 2021-2022 — expected all-five/all-three = 59; SHA256 `f2e2a957c7674c5626eff71c7ab360e698c1ccb9f8054383e5c5c0eb6498a86f`
- 2022-2023 — expected 61; SHA256 `899992d57d65c4786b47057a8c009dbeea3ec170aadc29beb1fd47e4b12a7524`
- 2023-2024 — expected 139; SHA256 `85e3b6d3cf2f3e54e1adbe456e24ec5042946e5102c99501c412039279814792`
- 2024-2025 — expected 135; SHA256 `3daf737dd36ef95b4e2e0d354674842faec755016e43abb9f987d67b1d594060`

Fixed development zero-label inventory: **394** all-five/all-three events.

Sealed reserve:
- 2025-2026 — expected 116; SHA256 `2c0c85bf0856b2e13de7a862a6a1e83ccd6aa0781d3aa2058ee289433f4c2053`

Excluded:
- 2020-2021 — N14W all-five/all-three = 36. It is not used for training, scoring, confirmation, replacement, or later threshold repair under this data plan.

## PASS gate
`C072N14WR1_ALEAGUE_WOMEN_DEV_PLAN_ZERO_LABEL_PASS` requires exact reproduction from the immutable N14W summary of ALL:
1. N14W terminal remains STOP;
2. target/result values materialized = 0 and model_fit/model_score=0;
3. exact six source SHA256 values match the N14W authoritative run;
4. exact development all-five counts are 59,61,139,135;
5. fixed development pooled count =394;
6. each selected development season has >=50 all-five/all-three events;
7. sealed 2025-2026 reserve count =116 and >=100;
8. 2020-2021 remains excluded and its observed count remains 36;
9. C073-C077 scientific results remain unused;
10. C070-F Confirmation1597/protected remain sealed.

No threshold is lowered. If the exact immutable evidence does not match, STOP.

## Downstream authorization if PASS
PASS authorizes exactly one next action: freeze a scientific contract for the post-view **structurally constrained O/U-tail `P(T)` reconstruction** before any development `TOTAL_GOALS` value is accessed.

The successor must preserve 2025-2026 as a target-unread one-shot reserve and must not use the N13 men's labels to choose any distribution formula, tail closure, threshold, weight, line subset, smoothing parameter or scoring gate.
