# Composite Draw Automatic Research R1.4 — post-Codex remediation

Status: `GPT_REMEDIATED_PENDING_CODEX`.

The exact HEAD `279a3380039c636e6c9fd29dcd84d760306c6a8f` received the overall Codex verdict `CODEX_FULL_RECHECK_FAILED_R1_4_DO_NOT_AUTHORIZE`. This document records the replacement execution contract only. It is not a Codex PASS and does not authorize real labels, training, scoring, automatic research, Ready conversion, merge or formal promotion.

The track remains `VIEWED_DEVELOPMENT_DATA` with `formal_weight=0`. The comparison baseline remains `INDEPENDENT_ELO_HISTORICAL_DRAW_BASELINE_R1`, which is candidate-independent and is not the current formal model. Any future result may only claim comparison against that research baseline.

The candidate catalog remains the deterministic 10×5×4 product: ten feature profiles, five positive-class weights and four nonlinear basis variants. The redundant `draw_logit_offset` dimension remains prohibited. Prediction fingerprints are recorded and equivalent outputs are not counted as independent completed candidates.

All preprocessing and inner selection remain confined to outer-training history. Outer evaluation rows cannot determine missing indicators, imputation, standardization, near-zero-variance removal or duplicate/correlation removal.

## Terminal non-candidate failures

Every nonzero controller exit is a terminal production event. The wrapper must create `run_failure_receipt.json`, append a `RUN_FAILURE` ledger record, atomically convert an existing checkpoint from `RUNNING` to `FAILED_RUNTIME` or `FAILED_SAFETY`, record an explicit `stop_reason`, and regenerate the manifest after all failure evidence is present. `probe` checks the failure receipt before consulting checkpoint status. A `RUNNING` checkpoint plus exit 1 and a `RUNNING` checkpoint plus exit 2 are mandatory counterexamples. Exit 2 is a real production path reached when candidate-result safety validation fails.

## Artifact universe and recovery

An Artifact or restored Cache is valid only when its actual regular-file set is exactly `manifest.files` plus `manifest.json`. Validation rejects missing or unregistered files, absolute paths, parent traversal, duplicate JSON or ZIP paths, symbolic links and any registered SHA-256 mismatch. Every checkpoint-referenced result, ledger, failure receipt and completed batch summary must be registered.

Artifact fallback is fail-closed and distinguishes four outcomes:

- a successful API query confirming no historical matching Artifact permits a first `NEW` state;
- API query failure terminates;
- download or extraction failure terminates;
- matching Artifacts that are all identity-incompatible terminate;
- integrity failure or tampering terminates;
- only a fully compatible Artifact may be restored and continued.

Identity rejection must never silently restart from candidate 1.

## Workflow mode and concurrency

The workflow event contract is frozen:

- a push containing the valid authorization-only commit may start research;
- `workflow_dispatch` with `mode=research` and valid authorization may restore or continue research;
- `workflow_dispatch` with `mode=preflight` remains zero-label Preflight even if the authorization file exists, and the automatic research job is skipped;
- pull requests remain Preflight-only.

Concurrency is declared once at workflow level. The group is workflow plus branch/ref and intentionally excludes SHA, `queue: max` is enabled, `cancel-in-progress: false` remains fixed, and the 14-slot matrix remains serial with `max-parallel: 1`.

## Cross-platform evidence

All Python test reads use explicit UTF-8 where text is read. Generated evidence uses LF, prediction fingerprints canonicalize to little-endian float64 rounded to 12 decimal places, and synthetic evidence is compared as canonical JSON. Ubuntu and Windows jobs must derive the same registered canonical digest.

## Authorization boundary

The authorization file remains absent. Real labels read, training runs, scoring runs and automatic research runs remain `0/0/0/0`. Formal model, formal data, config and CURRENT changes remain `0/0/0/0`. The PR remains Open, Draft and unmerged. A new exact HEAD must pass zero-label Actions and Artifact verification and then receive a fresh overall Codex review before authorization can be reconsidered.
