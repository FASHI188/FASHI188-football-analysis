# Composite Draw Automatic Research R1.4

R1.4 replaces the Codex-rejected R1.3 execution object. It remains a `VIEWED_DEVELOPMENT_DATA` research track with `formal_weight=0`.

The production Preflight directly references `.github/workflows/football-draw-auto-research-r1.yml`; no wrapper or monkeypatch changes the production path during tests.

The baseline is `INDEPENDENT_ELO_HISTORICAL_DRAW_BASELINE_R1`: Laplace-smoothed outer-training draw rate with pre-match Elo H/A allocation. It is candidate-independent and is not the current formal model. Therefore results may only claim improvement over this independent research baseline.

The candidate catalog is a deterministic 10×5×4 product: ten feature profiles, five positive-class weights and four genuinely different nonlinear basis variants. The mathematically redundant `draw_logit_offset` dimension is deleted. Prediction fingerprints are recorded; equivalent predictions are ledgered but not counted as independent completed candidates.

All preprocessing and inner selection use only outer-training history. The outer evaluation rows cannot determine missing indicators, imputation, standardization, near-zero-variance removal or duplicate/correlation removal.

The workflow uses standard `ubuntu-latest`, workflow-level concurrency with `cancel-in-progress: false`, and a 14-slot matrix with `max-parallel: 1`. The matrix jobs do not have job-level concurrency. Checkpoint restore first uses cache, then a real Artifact fallback that verifies manifest identity and every file hash; old frozen HEADs and wrong authorization digests are rejected.

Controller exit codes 1 and 2 remain failures. A wrapper creates a run-failure receipt and ledger record before evidence upload; after upload the workflow restores the original nonzero exit code. An exception before checkpoint creation is therefore auditable and stops continuation.

Top 5 is ranking only. A unique challenger is emitted only after passing the frozen minimum-improvement, probability-quality and cross-league stability gate. If no candidate passes, the final output is `NO_CHALLENGER`.
