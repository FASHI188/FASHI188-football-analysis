# C072-N12 — run-1 engineering STOP adjudication

Project: football3 only
Frozen scientific contract: `C072N12_ALEAGUE_DYNAMIC_SURFACE_PT_DEVELOPMENT_CONTRACT.md`
Run: `32252207602`
Job: `96065582551`

## Terminal
`C072N12_RUN1_ENGINEERING_STOP_BEFORE_MODEL_FIT`

The first N12 execution stopped before any model fit or score because the target reader treated repeated blank `TOTAL_GOALS` cells across market rows as separate invalid target attempts for the same authorized match.

Observed execution metadata before abort:
- zero-label-authorized development events: 782
- TOTAL_GOALS cell reads attempted on authorized development events: 1,495
- authorized events for which a valid TOTAL_GOALS value was eventually obtained: 779
- blank/invalid row-level attempts counted by the buggy reader: 716
- authorized events with no valid TOTAL_GOALS found: 3
- 2025-2026 TOTAL_GOALS values read: 0
- forbidden non-TOTAL_GOALS outcome values read: 0
- model fits: 0
- model scores: 0

No scientific metric was produced. This run is not a scientific PASS/FAIL/PARK.

Development TOTAL_GOALS labels that were read are now globally consumed development labels and remain development-only. The 2025-2026 target pool remains unopened.

## Allowed repair boundary
A versioned engineering R1 may repair only event-level target extraction while preserving every frozen scientific choice:
- source revision and hashes;
- zero-label market eligibility;
- quote transform;
- baseline/candidate features;
- estimator and C;
- folds;
- metrics;
- bootstrap;
- scientific gates;
- breakthrough threshold;
- 2025-2026 reserve boundary.

The repair may not use `HOME_SCORE`, `AWAY_SCORE`, `IS_WINNER`, `RUNNER_STATUS` or any other outcome/settlement field to fill missing TOTAL_GOALS.

The three authorized development matches for which no valid TOTAL_GOALS exists must be excluded from BOTH baseline and candidate without replacement. Their absence is a target-availability exclusion, not a performance-selected exclusion.
