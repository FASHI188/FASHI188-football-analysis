# FOOTBALL3 INDEPENDENT CURRENT

Updated: 2026-08-20 Asia/Taipei
Project: `football3`
Status: `FULL_STACK_ROOT_CAUSE_REMEDIATION_V3_REAUDIT_COMPLETE_NO_NEW_SCIENCE`

## 1. Independent lineage
Football3 scientific root remains immutable:
- experiment: `C072-C`
- root branch: `research/c072c-xg-total-scalar-20260818`
- root SHA: `e3e73c998020beef585cc459a69ea5b73b44ddb3`
- valid continuation: `C072-C -> football3/...`

C073-C077 and descendants remain quarantined. Cross-project information is allowed only for explicit comparison or global-consumption exclusion. The third-pass remediation adds executable merge-ancestry and stable patch-id cherry-pick checks; missing quarantine refs fail closed.

## 2. Scientific state is unchanged
Latest executed scientific experiment remains C072-N20, PR #330.

N20 terminal: `C072N20_P1000_PILOT_NO_SIGNAL`.
- exact cohort 1000; ordered identity SHA `a49e61df94d0f9c368b314829901f0d64d69ad25c51813551a298307e15e56cf`;
- dLogLoss `+0.0041598079`;
- bootstrap90 `[-0.0003677775,+0.0086930374]`;
- dBrier `+0.0008985958`;
- dRPS `+0.0001791452`;
- source LogLoss wins 1/4.

N20 remains PARKed and its 1000 target labels remain globally consumed. No same-label rescue is allowed.

## 3. Primary scientific target
Primary target remains complete match-level pre-match total-goals probability quality:

`P(T=0,1,2,3,4,5,6,7+)`.

Fixed chain:

`P(T) -> P(home-goal allocation | T,X) -> joint P(H,A) -> derived Draw/1X2/exact-score diagnostics`.

Draw Top1 remains downstream. Manual Draw/0-0/1-1/T=2 boosts, post-hoc thresholds/class weights and sacrificing proper score for Top1 remain forbidden.

## 4. Binding product contract
Comparable final-prematch research is frozen at **T-15m**. Baseline and candidate must both use information available by kickoff minus 15 minutes. Changing cutoff creates a different product contract.

Fresh science must use a strong same-cutoff market anchor, latest available snapshot at or before T-15m, de-vigged, exact representation frozen before labels, with a timezone-aware quote timestamp.

## 5. Third-pass re-audit found real defects in the previous remediation
The earlier green CI was not accepted as proof of correctness. A new reverse audit found and corrected these execution defects:

1. GitHub `pull_request.branches` was filtering the **base** branch, so a normal `football3/... -> main` PR could skip football3 policy/full-stack checks.
2. changed-runner detection trusted `run_`/`evaluate_` filename prefixes, so renamed scientific executables could bypass V2 preflight.
3. an unchanged runner plus a changed contract/audit artifact could avoid experiment revalidation.
4. non-finite `NaN`/`Inf` numeric contract gates could exploit comparison semantics and bypass some checks.
5. identity-lock validation checked file SHA but did not prove the file was actually a semantic identity list.
6. contract branch was not bound to the actual GitHub runtime branch.
7. C072-C ancestry checks did not automatically catch C073-C077 merge/cherry-pick contamination.
8. timezone-naive timestamps could be interpreted as UTC by pandas.
9. some core inputs could silently truncate noninteger total goals or broadcast mismatched O/U arrays.
10. success/calibration/domain gates were partly declarative; a runner could import the shared core but still score through a separate implementation.

These are now corrected without opening target labels or sealed pools.

## 6. Canonical runtime execution
`football3_core.py` now provides the binding `evaluate_frozen_experiment` path for new scoring runners. A compliant runner must actually call:
- `assert_master_prediction_cutoff`;
- `assert_feature_pit`;
- `assert_temporal_oos`;
- `evaluate_frozen_experiment`.

The canonical evaluator applies frozen calibration bins, LogLoss/Brier/RPS, Top1ECE/ClasswiseECE, common paired match bootstrap for all three proper scores, temporal-fold diagnostics, domain diagnostics and pre-registered numerical gates. Top1/Top3 remain diagnostics only.

Core guards now also reject timezone-naive timestamps, noninteger total-goal labels, O/U shape/broadcast mismatch, duplicate O/U lines, score/target length mismatch, invalid bootstrap argument types and invalid power-planning parameters.

## 7. Scientific-code surface is fail-closed
On a football3 scientific PR:
- every active V2 experiment contract is revalidated, even if its scoring runner did not change;
- changed research Python must bind to an active experiment contract as the scoring runner or an explicit helper;
- alternate new notebook/shell/R execution surfaces are blocked until an equally strict execution standard exists;
- changed scoring runners must use the canonical runtime guards/evaluator;
- sealed-pool tokens remain forbidden in new runners.

Historical runners/workflows remain only for provenance/reproduction and do not become current authority.

## 8. Semantic global-consumption boundary
A zero-label identity lock now uses `sha256_csv_v1`:
- exact `identity_sha256` header;
- one lowercase 64-hex identity per row;
- no duplicates/blanks;
- positive identity count;
- matching ordered identity digest;
- matching file SHA256.

The zero-label global-consumption audit artifact must bind to that exact identity set and source revision and carry structured connected GitHub/Airtable receipts. GitHub CI validates artifact structure, binding and hashes; it does not pretend to cryptographically prove external-source truth. That truth remains the responsibility of the connected zero-label audit which produced the receipts.

Any consumed overlap or unresolved historical identity gap forces REPLICATION/REPRODUCTION. Fresh confirmation requires both zero.

## 9. CI and project-scope correction
Football3 workflows no longer rely on `pull_request.branches: football3/**`. Relevant-path PRs trigger independently of the base branch, while the full football3 job gates on `github.head_ref` so `football3/... -> main` is covered.

A separate scope guard prevents non-football3 branches from mutating football3-owned policy/execution surfaces while leaving `FOOTBALL_GLOBAL_CONSUMPTION_REGISTRY_V1.json` cross-project writable.

CI additionally runs `audit_football3_lineage.py` to detect both direct quarantined ancestry and stable scientific patch-id overlap/cherry-picks.

## 10. Validation/sample rules
Before labels, frozen gates cover:
- primary LogLoss delta and paired-bootstrap LogLoss upper CI;
- Brier and RPS non-inferiority;
- Top1ECE and ClasswiseECE non-inferiority;
- temporal minimum fold rows and fold win fraction;
- domain minimum count/rows, win fraction and maximum domain LogLoss regression.

The canonical evaluator also reports paired bootstrap intervals for Brier and RPS.

Confirmation requires positive minimum N, planned power in `[0.80,1)`, alpha in `(0,0.20]`, DEVELOPMENT_ONLY or EXTERNAL_PRIOR planning basis, immutable planning artifact/SHA and no optional stopping.

## 11. Historical evidence interpretation remains unchanged
No historical target labels were reopened or rescored.
- C072-F2 remains later/closing-market-vs-opening evidence, not same-cutoff T-15m alpha proof.
- C072-I2 `D|T` component evidence remains retained; its historical `.T` issue was corrected before its one-shot execution.
- C072-K2 remains historical LogLoss/Brier evidence, not a full current V2 PASS.
- C072-N20 remains `PILOT_NO_SIGNAL/PARK`.

## 12. Sealed boundaries
Remain sealed/unopened:
- C070-F Confirmation1597;
- N17 reserve266;
- N18C confirmation150;
- any other protected football3 pool not explicitly authorized.

## 13. Third-pass validated implementation receipt
Validated implementation HEAD before this receipt-only commit:

`4eb094814a1d65151a0fc12b9692a1f835c450fb`

GitHub Actions on that exact implementation head:
- `Football3 Research Policy Integrity` run `32331727934`: **SUCCESS**;
- `Football3 Full Stack Scientific Preflight` run `32331728253`: **SUCCESS**;
- `Football Engineering Quality and Security` run `32331727927`: **SUCCESS**.

Full-stack evidence:
- compile: PASS;
- canonical/core/contract regression tests: **22/22 PASS**;
- zero-real-label canonical evaluator smoke: PASS;
- semantic PIT/master-cutoff/identity/consumption/metric gates: PASS;
- historical/active execution-surface audit: PASS, warnings = 0;
- V3 policy + V2 runtime contract + single-authority audit: PASS;
- changed-code binding and all-active-V2-contract revalidation: PASS;
- no-real-target/no-sealed proof: PASS.

Lineage isolation evidence:
- C073-C077 quarantine refs checked: **34**;
- direct quarantined ancestry detected: **0**;
- stable scientific patch-id/cherry-pick matches detected: **0**;
- lineage blockers: **0**.

Scientific activity during this third-pass re-audit:
- new real target labels opened: 0;
- real-data model fits/scoring: 0/0;
- sealed pools opened: 0;
- C073-C077 scientific evidence imported: 0.

This commit changes the receipt/current text only. The receipt-only HEAD must itself retain green CI before this remediation checkpoint is treated as fully closed.

## 14. Next scientific step
This re-audit repairs the research/execution engine. It does **not** solve the predictive P(T) problem.

After the receipt-only HEAD is verified green, a separately authorized new experiment may start. It must be a materially new P(T) information/measurement hypothesis under the T-15m V2 protocol. Latent `P(T)` plus a noisy market-measurement layer remains a candidate hypothesis, not a result or PASS.

formal_weight remains 0. No downstream Draw optimization is the next step.
