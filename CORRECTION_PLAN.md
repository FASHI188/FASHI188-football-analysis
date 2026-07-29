# 1X2 Audit Correction Plan

Scope is strictly corrective. No new model training, no parameter tuning, no 5524-match rerun, and no CURRENT change.

## Corrections

1. Separate FULL_1X2 from SELECTIVE_1X2 everywhere. Reserve `51.7613%` for the 5110-match full market-argmax sample and rename `70.77%` as **精选主客胜准确率70.77%@24.50%市场覆盖率** (`1252/5110`; `22.66%` of all 5524 matches).
2. State explicitly that the frozen selector structurally excludes Draw from selected output: direction is fixed by market argmax; selector only SELECT/ABSTAIN; all frozen Draw reliability values are below the `0.55` threshold.
3. Rename V6.50.9 conceptually to **高风险H/A弃权器**. Its 359 abstentions remove 95 actual draws, 69 opposite H/A outcomes, and 195 originally correct H/A picks. It predicts zero draws; retained 1252 contains 216 actual draws, so Draw remains 216/366 = 59.02% of retained errors.
4. Modify `v6_fullseason_2025_replay_v6506.py::x12_metrics()` for future runs only to persist `confusion_matrix[predicted][actual]`, per-class precision/recall/F1, macro-F1, and balanced accuracy. Do not execute the replay.
5. Add explicit scorecard semantics:
   - `FULL_1X2`: every eligible match gets H/D/A, no ABSTAIN; accuracy, Log Loss, Brier, RPS, confusion matrix, class recalls.
   - `SELECTIVE_1X2`: ABSTAIN allowed; accuracy, executed count, total count, coverage, and accuracy-coverage curve. Selective accuracy must never be reported as FULL_1X2 accuracy.
6. Reclassify reused 2025 research target as **时间外风格的重复研究集** rather than untouched/final blind holdout. Preserve the fact that target labels were not directly used to fit the frozen threshold while acknowledging repeated analyst exposure across V6.50.7-V6.51.2.
7. Keep historical closing-odds evidence labeled `RETROSPECTIVE_REFERENCE_ONLY`; it cannot establish live fixed-time reproducibility or formal promotion.
8. Restore the existing V6.50.9 status receipt to a reachable path on `main` from existing evidence/history only; do not regenerate it.
9. Correct `DRAW_AUDIT_HANDOFF.md` and draw-problem resolution status wording so neither `70.77%` nor the veto is described as complete 1X2 performance or a solved Draw-direction problem.

## Validation of this correction

- Static code/file inspection only.
- Verify the evaluation code compiles by inspection; do not run the 5524 replay.
- Verify restored V6.50.9 receipt is byte/content traceable to existing historical evidence rather than newly recomputed output.
- Formal CURRENT remains V5.0.1 and unchanged.
