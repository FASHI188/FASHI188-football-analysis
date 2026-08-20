# Football3 execution standard V2

This is the single binding execution standard for new football3 science after the 2026-08-20 root-cause remediation and cross-layer re-audit. It does not retroactively rewrite historical target outcomes.

## 1. One product task, one master cutoff
Comparable final-prematch research is frozen at **T-15m**. Baseline and candidate must use only information observable no later than kickoff minus 15 minutes. A different cutoff is a different product contract.

Every market quote and candidate feature must have a timezone-aware PIT timestamp. Missing, invalid, timezone-naive or post-cutoff timestamps fail closed.

## 2. One experiment, one V2 contract
Every new football3 scoring runner declares exactly one `FOOTBALL3_EXPERIMENT_CONTRACT`. The contract freezes branch, root, scientific question, baseline, candidate feature set, **feature-to-timestamp map**, source revision, exact scored identity cohort, OOS design, metrics, numerical gates, sample plan and stopping rule.

The declared branch must equal the GitHub runtime head. A football3 experiment cannot be made new by renaming its branch, runner, helper or workflow.

## 3. Scientific execution surface is repository-wide fail-closed
For a `football3/...` PR, relevant changes under `football-data/**`, `scripts/**` and `.github/workflows/**` trigger the full-stack gate.

Python execution code under `football-data/` or `scripts/` is not trusted by filename. Only a small exact infrastructure allow-list is exempt. Every other changed Python executable must bind to an active V2 contract as the single scoring runner or an explicit helper. New notebook/shell/R/JS/TS/PowerShell/batch executable surfaces are blocked until an equally strict execution standard exists.

A football3 branch may not create a differently named workflow to escape the `football3-*` workflow audit surface. Every active V2 contract is revalidated on every football3 scientific PR, including contract-only or audit-artifact-only changes.

## 4. Strong same-cutoff market baseline and input-semantics audit
A free-text baseline declaration is insufficient. The baseline must be a market anchor at T-15m, use the latest available snapshot at or before T-15m, be de-vigged and have its exact representation frozen before labels.

Before target access, `FOOTBALL3_INPUT_SEMANTICS_AUDIT_TEMPLATE_V1.json` is instantiated as a zero-label connected audit artifact. It binds the frozen identity set to:
- the exact baseline representation and quote timestamp column;
- zero missing/post-cutoff baseline quote timestamps;
- the candidate's complete feature list;
- the exact feature-to-timestamp map;
- zero missing/post-cutoff candidate feature timestamps.

CI verifies structure, hashes and binding. It **cannot independently prove external-source semantics** such as whether a provider's record truly is the latest available quote; that fact must be established by the connected zero-label audit evidence. This limitation must never be reported as an automatically proven fact.

## 5. Canonical runtime scoring path
New scoring runners must have a reachable execution path that invokes:
- `assert_master_prediction_cutoff`;
- `assert_feature_pit` using the frozen literal timestamp-column set;
- `assert_temporal_oos`;
- `assert_exact_one_to_one_join`;
- `assert_sealed_boundaries`;
- `evaluate_frozen_experiment`.

The canonical evaluator requires the **actual ordered scoring identity hashes**. Before any metric is computed, scoring row count and ordered identity digest must exactly match the frozen identity lock. This prevents locking cohort A and scoring cohort B.

The evaluator also enforces the actual runtime development/confirmation minimum N, LogLoss/Brier/RPS, Top1ECE/ClasswiseECE, paired bootstrap for all three proper scores, temporal-fold diagnostics, domain diagnostics and frozen numerical promotion gates. Top1/Top3 remain diagnostics only.

Static AST checks reduce obvious bypasses but do not constitute a mathematical proof of arbitrary Python control-flow semantics. Runtime identity/sample binding, zero-label semantic audit, CI tests and code review remain complementary controls.

## 6. Semantic identity lock
`sha256_csv_v1` requires exact header `identity_sha256`, one lowercase 64-hex hash per row, no duplicates/blanks, matching positive count, matching ordered identity digest and matching file SHA256.

For a scoring experiment, the lock represents the **exact rows that will be scored**, not an unrelated superset containing training-only rows. Training history may be frozen separately, but cannot substitute for the scored-identity lock.

## 7. Global consumption and cross-project duplication
Before target access, the connected zero-label audit must check the minimum global registry plus GitHub and Airtable history. It must examine source repository, revision, seasons, match identities, market fields, label definition and whether the same scientific hypothesis was already viewed.

Any consumed identity overlap, unresolved historical identity gap, previously viewed same scientific hypothesis, or audit conclusion that the experiment is replication/reproduction forbids a fresh evidence classification. Viewed labels stay globally consumed across football projects.

GitHub CI verifies the audit artifact's structure/binding/hash, not the truth/completeness of the external search. The external truth must come from the connected audit that produced the receipts.

## 8. Lineage isolation includes descendants
The quarantine is **lineage-based, not branch-name-based**. C073-C077 are seed quarantines. Every later `research/*` branch containing seed-lineage commits is also quarantined, including C078/C079 descendants and future renamed descendants.

CI requires C072-C ancestry, enumerates seed refs, derives descendant refs by commit ancestry, rejects direct quarantined ancestry in football3 and compares stable scientific patch IDs to catch cherry-picks/rebases. Missing seed refs fail closed.

## 9. Confirmation sample and power rule
Arbitrary 100/200/300 confirmation packets are not accepted by convenience. Before confirmation labels, a `football3_paired_power_plan_v1` artifact freezes effect magnitude, paired-delta SD, alpha, planned power, planning basis, conservative multiplier and required N.

The validator **recomputes required N** from those frozen values and rejects a self-reported `required_n` that does not match the formula or a contract minimum N below the recomputed value. The canonical evaluator then rejects an actual confirmation sample smaller than that frozen minimum. Optional stopping remains forbidden.

## 10. Proper-score, calibration, temporal and domain gates
Primary success is LogLoss. Pre-registered point-delta and bootstrap upper-CI gates may not permit LogLoss worsening. Brier, RPS, Top1ECE and ClasswiseECE are mandatory non-inferiority gates. Temporal fold minimum rows/win fraction and domain minimum count/rows, win fraction and maximum LogLoss regression are frozen before labels.

The canonical evaluator reports paired bootstrap intervals for LogLoss, Brier and RPS.

## 11. Sealed boundaries
C070-F Confirmation1597, N17 reserve266 and N18C confirmation150 remain sealed unless the user explicitly authorizes access. Known sealed tokens are rejected in new scoring runners in addition to runtime access guards.

## 12. Method-shopping prohibition
After labels are viewed, the same labels cannot be rescued by changing hyperparameters, feature set, feature timestamp map, windows, O/U lines, smoothing, thresholds/classes, distribution family, league/source subset, dispersion equation, neighboring transform, model shell, metric/gate, calibration or stopping rule.

## 13. Historical evidence boundary
Historical football3 workflows/runners remain provenance/reproduction only. They are not automatically upgraded to this V2 contract. C072-N20 remains `PILOT_NO_SIGNAL/PARK`.

## 14. Scientific interpretation boundary
These controls reduce invalid inference; they do not solve match-level P(T). The next separately authorized science must still be a materially new P(T) information/measurement hypothesis under the T-15m contract. Direct Draw/0-0/1-1 boosting remains prohibited.
