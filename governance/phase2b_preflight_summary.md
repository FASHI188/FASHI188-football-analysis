# Phase-2B-0 Preflight Repair Status

Repository: `FASHI188/FASHI188-football-analysis`

Branch: `codex/repository-governance-phase2b-preflight`

Frozen main: `c9311d3c33d37f0ff52774ecfc4a7816209e3a2a`

Observed main: `6beea539280293c8e414a3c2eaa2f0f8dd558942`

## Ledger repair

- `governance/main_drift_commit_ledger.json`: rebuilt as plain JSON with 161 direct commit records.
- `governance/main_drift_file_ledger.json`: rebuilt as plain JSON with 161 direct file records.
- gzip/base64 payloads are no longer used.
- All 161 commit records now store `committer: "GitHub"` rather than the incorrect `web-flow` default.
- Classification remains 159 `GENERATED_EVIDENCE_PRESERVE`, 2 `CONFIG_OR_RUNTIME_CHANGE_REVIEW`, 0 `UNKNOWN`.
- File classes remain 132 EVIDENCE, 19 MANIFEST, 8 FORWARD, 2 CONFIG.

## Validator

`scripts/governance/validate_phase2b_preflight.py` is Git-history authoritative. It checks the exact 161-commit order and set, raw Git author/committer names, author/committer timestamps, messages, changed files, the 161 file records, join integrity, recomputed classification counts, UNKNOWN=0, and governance JSON parseability. Any mismatch or missing Git object returns a non-zero exit code.

The current execution container does not contain the repository Git object database and cannot resolve/fetch GitHub from the shell. Therefore the validator cannot truthfully complete the required raw-Git comparison here; its current execution is a hard failure with exit code 2.

Accordingly:

- `all_results_recomputable_from_governance_files = false`
- `allow_true_phase2b = false`
- true Phase-2B has **not** started.
- no legacy workflow was modified or deleted.
- main, formal CURRENT, PR state, and repository-level Actions state were not changed.

A real repository checkout must run the validator to exit code 0 before either admission boolean may be changed to `true`.
