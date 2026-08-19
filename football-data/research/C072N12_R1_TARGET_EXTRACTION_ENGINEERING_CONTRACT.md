# C072-N12 R1 — event-level TOTAL_GOALS extraction engineering repair

Project: football3 only
Parent scientific contract: `C072N12_ALEAGUE_DYNAMIC_SURFACE_PT_DEVELOPMENT_CONTRACT.md`
Parent execution: run `32252207602`, engineering STOP before model fit.

## Scope
R1 changes no scientific method. It repairs only duplicate row-level target extraction for the already-authorized 2020-21..2024-25 development identities.

## Frozen event-level extraction rule
For each authorized development `(season, EVENT_ID)`:
1. inspect only its `TOTAL_GOALS` cells in the same pinned source file;
2. ignore empty or non-finite/non-integer/negative cells as unavailable cells;
3. collect all valid nonnegative integer TOTAL_GOALS values observed for that EVENT_ID;
4. require the set of valid values to have cardinality exactly 1;
5. if exactly one distinct valid value exists, use `T=min(value,7)`;
6. if zero valid values exist, exclude the event from both baseline and candidate, without replacement;
7. if >1 distinct valid values exist, fail closed and STOP before model fit.

No other target/result/settlement column may be read.

## Frozen missing-target handling
The run-1 audit established 3 authorized development events with no valid TOTAL_GOALS. R1 must report their `(season, EVENT_ID)` identities but not any forbidden outcome value. Those three are excluded symmetrically from baseline/candidate and from all folds. No replacement event may be selected.

Expected maximum usable development identities after this availability exclusion: 779.

## Reserve boundary
2025-2026 `TOTAL_GOALS` must remain completely unread. R1 must still report target_values_2025_2026_read=0.

## Scientific invariance
All model features, quote transform, estimator, C, chronological folds, metrics, 5,000-replicate EVENT_DATE cluster bootstrap seed 72012, PASS gates and practical breakthrough threshold dLogLoss <= -0.003 remain byte-for-byte in scientific meaning.

R1 is an engineering replay of the original frozen N12 experiment, not a new hypothesis and not a method search.
