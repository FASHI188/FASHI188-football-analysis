# Composite Draw Auto Research Plan R1.3

## Scope

This PR contains a real automatic research pipeline for the existing viewed development datasets. It does not claim a blind holdout, does not promote a formal model, does not change formal data/config/CURRENT, and keeps `formal_weight=0`.

## Automatic loop

One authorized workflow launch uses standard `ubuntu-latest` matrix slots with `max-parallel: 1`. Each active slot restores the newest checkpoint, evaluates the next part of the frozen 200-candidate catalog, records every success and failure, saves the checkpoint, and uploads a compact Artifact. Later slots continue without waiting for a user, GPT, or Codex message.

The serial matrix deliberately avoids cross-run self-dispatch and therefore does not require repository secrets or a workflow token with write permission. Duplicate workflow launches are serialized by concurrency with `cancel-in-progress: false`; they restore the latest checkpoint instead of starting from candidate 1.

## Candidate budget

The catalog is the deterministic product of ten feature profiles, five positive-class weights, and four draw-logit offsets: exactly 200 candidates. Every profile carries a frozen three-value L2 grid selected only inside the outer-training history.

## Validation

Validation is competition-season rolling nested validation. For each competition, seasons three through five are outer evaluation folds. The latest prior season is inner validation and all earlier seasons are inner training. There is no random split.

Missing indicators, imputation, standardization, near-zero-variance removal, and duplicate/high-correlation removal are fitted only on the relevant training rows. Outer evaluation rows are transformed only after those decisions are frozen.

## Checkpoints and stopping

A complete batch is 20 attempted candidates. A checkpoint is also written after each candidate so a controlled timeout can continue the same incomplete batch. Every complete batch generates a Markdown summary, full ledger update, Top 5, and remaining candidate/time budget.

The loop stops at 200 candidates, six cumulative research hours, three complete batches without the frozen minimum improvement, or any leakage/numerical/completeness/security failure.

## Outputs

The final Artifact contains checkpoints, the complete ledger, every candidate result and failure, per-fold and per-league metrics, calibration, batch summaries, Top 5, the unique recommended challenger, the stop reason, and whether future untouched data validation is worth waiting for. Large training datasets are never copied into Artifacts.

## Authorization identity

After Codex accepts the frozen code HEAD, the authorization-only child commit must bind the identity Git blob and every frozen contract, runner, workflow, validator, test, preregistration, receipt and report-plan entry. Missing or altered bindings fail closed before any label access.
