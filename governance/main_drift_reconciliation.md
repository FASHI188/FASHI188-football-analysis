# Phase-2B-0 Main Drift Reconciliation

## Scope

Repository: `FASHI188/FASHI188-football-analysis`

Frozen main: `c9311d3c33d37f0ff52774ecfc4a7816209e3a2a`
Observed/current main: `6beea539280293c8e414a3c2eaa2f0f8dd558942`
Range: `c9311d3c33d37f0ff52774ecfc4a7816209e3a2a..6beea539280293c8e414a3c2eaa2f0f8dd558942`

The current main SHA is unchanged from the previously observed SHA. No reset, revert, cherry-pick, merge, or force-push was performed.

## Reconciled counts

- Commits after frozen main: **161**
- GitHub Actions bot commits: **161**
- Human commits: **0**
- Workflow-change commits: **0**
- Model-code-change commits: **0**
- Formal CURRENT-change commits: **0**
- Pure evidence / forward / manifest / cache commits: **159**
- Config/runtime-change review commits: **2**
- Drift UNKNOWN: **0**

Classification:

- `GENERATED_EVIDENCE_PRESERVE`: **159**
- `GENERATED_DUPLICATE_REVIEW`: **0**
- `CONFIG_OR_RUNTIME_CHANGE_REVIEW`: **2**
- `WORKFLOW_CHANGE_REVIEW`: **0**
- `FORMAL_CURRENT_CONFLICT`: **0**
- `MODEL_CODE_CHANGE_REVIEW`: **0**
- `UNKNOWN`: **0**

## Config/runtime review

Exactly two post-freeze commits touch `football-data/config/`:

1. `8941efc4f7f53a1d499b9b960a09db9082064f7a`
   - `football-data/config/v6_full17_capture_identity_v6484.json`
   - generated timestamp/base-registry identity refresh only in the inspected diff.
   - retained runtime governance flags include `runtime_probability_change: false` and `current_rule_change: false`.

2. `04b298199a353a386428b16ce75b6caa3f699740`
   - `football-data/config/v6_full17_identity_registry_v6482.json`
   - generated timestamp refresh only in the inspected diff.
   - formal-current reference remains V5.0.1.

These commits are preserved in place on main and are **not** automatically imported into the governance branch.

## Reconciliation decision

The 159 generated evidence/forward/manifest commits are preserved as audit evidence. The two config/runtime commits are explicitly classified for review, not auto-merged. No workflow, model-code, or formal-CURRENT drift exists in this range.

The detailed one-record-per-commit ledger is `governance/main_drift_commit_ledger.json`.
The one-record-per-commit-file ledger is `governance/main_drift_file_ledger.json`.
