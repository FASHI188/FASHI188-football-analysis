# Football3 execution standard V2

This is the single binding execution standard for new football3 science after the 2026-08-20 root-cause remediation and third-pass re-audit. It does not retroactively rewrite historical target outcomes.

## 1. One product task, one master cutoff
The comparable final-prematch product task is frozen at **T-15m**. Baseline and candidate must both use information observable no later than kickoff minus 15 minutes. A different cutoff is a different product contract.

Every feature and quote timestamp must be present, parseable and **timezone-aware**. Timezone-naive, missing, invalid or post-cutoff timestamps fail closed; they are never silently interpreted as UTC.

## 2. One experiment, one V2 machine-readable contract
Every new football3 scoring runner declares a repo-relative `FOOTBALL3_EXPERIMENT_CONTRACT`. The contract must conform to `FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json` and the declared branch must equal the actual GitHub head/ref at execution time.

Every changed research Python file is fail-closed: it must be either the scoring runner bound to a V2 contract or an explicitly declared helper bound to that same active contract. New notebook/shell/R executable surfaces are blocked until an equally strict standard exists for them. Every active V2 contract is revalidated on every football3 scientific PR, even if only the contract or an audit artifact changed.

## 3. Strong same-cutoff market baseline
A free-text baseline description is insufficient. The baseline must be a market anchor, use T-15m, use the latest available snapshot at or before T-15m, be de-vigged, freeze the exact representation before labels and identify the timezone-aware quote timestamp column.

Opening-to-closing or earlier-to-later information gains are not same-cutoff alpha against the T-15m market baseline.

## 4. Canonical scientific execution path
New scoring runners must actually call the shared runtime guards and canonical evaluator, not merely import `football3_core`:
- `assert_master_prediction_cutoff`;
- `assert_feature_pit`;
- `assert_temporal_oos`;
- `evaluate_frozen_experiment`.

`evaluate_frozen_experiment` is the binding scoring/gating path. It applies the contract's calibration bins, LogLoss/Brier/RPS, Top1ECE/ClasswiseECE, a common paired match bootstrap for all three proper scores, temporal-fold diagnostics, domain diagnostics and the pre-registered numerical gates. Top1/Top3 remain diagnostics only.

The core additionally fails closed on noninteger total-goal labels, duplicate O/U lines, O/U array shape/broadcast mismatches, invalid probability matrices, scoring length mismatches, bad bootstrap types/seeds and invalid power-planning parameters.

## 5. Semantic zero-label identity lock
An identity lock is not accepted merely because a file has a SHA256. The format is `sha256_csv_v1`:
- exact header `identity_sha256`;
- one lowercase 64-hex identity per row;
- no duplicates or blanks;
- positive row count;
- contract `identity_count` must match;
- contract `ordered_identity_sha256` must match the ordered rows;
- the file SHA256 must also match.

Artifact paths must be repo-local relative paths under the contract directory. Absolute paths, `..` traversal and symlink escape are rejected.

## 6. Mandatory zero-label consumption audit
Before real target materialization:
1. freeze source revision and semantic identity lock;
2. check the minimum global registry;
3. perform connected GitHub history search;
4. perform connected Airtable history search;
5. write an immutable zero-label audit artifact with identity binding and structured receipts;
6. freeze evidence class and all numerical gates;
7. run synthetic canonical-evaluator smoke and full preflight;
8. obtain explicit user authorization;
9. execute once.

The GitHub validator checks the audit artifact's structure, identity/source binding and hashes. It cannot cryptographically prove the external GitHub/Airtable search itself; that truth must come from the connected zero-label audit that generated the structured receipts. If any consumed overlap or unresolved historical identity gap remains, fresh classification is forbidden and the run is REPLICATION/REPRODUCTION only.

A crash after target materialization consumes those identities globally even when no score is emitted. Engineering replay is reproduction.

## 7. Lineage isolation
A `football3/` prefix alone is insufficient. CI must verify:
- C072-C is an ancestor;
- C073-C077 quarantine refs are available;
- no quarantined commit is an ancestor of HEAD;
- no stable scientific patch-id from quarantined branches appears after C072-C on football3.

Missing quarantine refs fail closed. This catches both ordinary merges and cherry-picks that change commit SHA.

## 8. GitHub Actions trigger boundary
`pull_request.branches` filters the PR **base**, so football3 policy/full-stack workflows must not rely on `branches: football3/**`. The workflows trigger by relevant paths and gate on `github.head_ref` so a normal `football3/... -> main` PR is still checked.

A separate scope guard blocks non-football3 branches from changing football3-owned authority/execution files while leaving the shared global-consumption registry cross-project writable.

## 9. Confirmation power rule
Arbitrary 100/200/300 confirmation packets are not a default. Before confirmation labels, the contract freezes positive minimum N, planned power in `[0.80,1)`, alpha in `(0,0.20]`, DEVELOPMENT_ONLY or EXTERNAL_PRIOR planning basis, immutable planning artifact/SHA and no optional stopping.

## 10. Proper-score, calibration, temporal and domain gates
Primary success is LogLoss. Pre-registered LogLoss point delta and bootstrap upper-CI gates cannot permit worsening. Brier, RPS, Top1ECE and ClasswiseECE point deltas are mandatory non-inferiority gates. Temporal fold win fraction, minimum fold rows, domain minimum count/rows, domain win fraction and maximum tolerated domain LogLoss regression are frozen before labels.

The canonical evaluator reports bootstrap CIs for LogLoss, Brier and RPS even when only the pre-registered LogLoss CI is a formal promotion gate.

## 11. Sealed and consumed boundaries
C070-F Confirmation1597, N17 reserve266 and N18C confirmation150 remain sealed unless the user explicitly authorizes access. Known sealed tokens/paths are rejected in new scoring runners in addition to runtime access guards.

## 12. Historical workflows and evidence
Historical football3 workflows/runners remain in Git for provenance and reproduction. Their existence does not make them current authority and does not upgrade old results to the V2 contract. A historical manual replay remains reproduction on consumed evidence. New science enters only through the V2 path above.

## 13. Method-shopping prohibition
After labels are viewed, the same labels cannot be rescued by changing hyperparameters, features, windows, O/U lines, smoothing, thresholds/classes, distribution family, league/source subset, dispersion equation, neighboring transform, model shell, metric/gate, calibration scheme or stopping rule.

## 14. Scientific interpretation boundary
These controls reduce invalid inference; they do not solve P(T). C072-N20 remains `PILOT_NO_SIGNAL/PARK`. The next authorized science must be a materially new P(T) information/measurement hypothesis. Direct Draw/0-0/1-1 boosting remains prohibited.
