# Football3 historical positive-result engineering audit — 2026-08-20

Scope: read-only code/contract audit of the three strongest post-root positive results that materially influence the football3 diagnosis: C072-F2, C072-I2 and C072-K2. No target pool was reopened and no metric was recomputed in this remediation.

This audit separates **technical execution validity at the time** from **whether the old experiment answers the newly corrected same-cutoff scientific question**.

## C072-F2 — O/U2.5 opening-to-closing movement forward confirmation

Audited files:
- `evaluate_c072e2_ou25_movement_directt.py` (development dependency)
- `evaluate_c072f2_ou25_movement_forward_confirm.py`
- F2 contract/result/workflow in PR #276.

Execution checks:
- temporal split is season-forward: historical rows train, 2024/25 is test;
- target is explicitly collapsed to 0..6,7+ through the E2 dependency;
- candidate/reference class probabilities are expanded to a fixed eight-class order and renormalized;
- LogLoss, multiclass Brier and normalized RPS are all computed;
- bootstrap is paired at match level with a fixed seed;
- probability-conservation residual is checked;
- no random split was found;
- no N20-style target-column `.T` error was found in the executed F2 path.

Binding limitation under the corrected research contract:
- source metadata explicitly classifies PIT as `COARSE_OPEN_CLOSE_SEMANTICS_ONLY_NO_IMMUTABLE_QUOTE_TIMESTAMPS`;
- the reference contains opening O/U2.5 and the candidate adds `close-open`, so the candidate can algebraically recover the closing level;
- therefore F2 remains evidence that later/closing market information materially improves P(T) over an earlier opening reference, but it is **not** evidence of incremental alpha beyond a same-cutoff closing-market baseline.

Audit status: `TECHNICALLY_EXECUTED_AS_CONTRACTED_WITH_PIT_LIMITATION`; keep as historical component evidence, do not use as same-cutoff confirmation.

## C072-I2 — D|T / home-goal allocation forward confirmation

Audited files:
- `evaluate_c072i2_dgiven_t_forward_confirm.py`
- `run_c072i2_dgiven_t_forward_confirm.py`
- `.github/workflows/football3-c072i2-dgiven-t-forward-confirm.yml`
- I2 contract/result in PR #279.

Important historical engineering finding:
- the raw evaluator contains the pandas ambiguity `scored.T` / `even.T`, where `DataFrame.T` means transpose rather than the target column named `T`;
- **before the one-shot confirmation execution**, the committed wrapper `run_c072i2_dgiven_t_forward_confirm.py` performs exactly two engineering substitutions: `scored.T.map -> scored['T'].map` and `even.T//2 -> even['T']//2`;
- the confirmation workflow explicitly executes that corrected wrapper, not the raw evaluator.

Execution checks on the path actually run:
- identity gate occurs before target interpretation;
- historical warmup is updated strictly after same-date predictions, preventing same-date result leakage into earlier predictions;
- exact-T groups 1..6 are modeled separately with legal support checks;
- LogLoss, Brier and normalized RPS are all calculated;
- bootstrap is paired match-level with fixed seed;
- chronological halves, division consistency and exact-T consistency are required;
- model coefficients are not refit on confirmation;
- C070-F1597 remains sealed.

Audit status: `TECHNICALLY_VALID_EXECUTION_WITH_PREEXECUTION_ENGINEERING_WRAPPER`. I2 proper-score evidence remains usable as football3 component evidence. The repeated `.T` defect is now elevated to a repository-wide preflight prohibition so it cannot recur as an experiment-local patch.

## C072-K2 — joint low-score integration confirmation

Audited file:
- `evaluate_c072k2_joint_low_score_confirm.py` plus K2 contract/result/workflow in PR #281.

Execution checks:
- identity gate precedes first target opening;
- same-date historical features are predict-before-update;
- joint probability matrix is explicitly normalized and conservation is audited;
- bootstrap is paired match-level with frozen seeds;
- early/late chronological halves and division consistency are checked;
- component coefficients are not refit on confirmation;
- no random split or N20-style `.T` target-column bug was found in the K2 executed script.

Corrected-standard limitations:
1. K2's `generic_metrics`/gate reports LogLoss and Brier but **does not compute RPS** for the joint 29-state/conditional 28-score target. Therefore K2 cannot be described under the new policy as having passed the full mandatory `LogLoss+Brier+RPS` proper-score suite. Its observed LogLoss/Brier evidence remains historical evidence; the missing RPS cannot be silently imputed after the fact.
2. The P(T) BASE/BOTH comparison inherits E2/F2 opening-versus-opening+movement construction. It therefore includes later/closing market information rather than isolating increment beyond a same-cutoff closing anchor.
3. The market data have coarse opening/closing semantics rather than immutable quote timestamps.

Audit status: `HISTORICAL_LL_BRIER_PASS_WITH_RPS_AND_SAME_CUTOFF_LIMITATIONS`. K2 must not be used as proof that the full corrected end-to-end contract has already passed.

## Binding remediation outcome

The historical results are not rewritten or re-scored on viewed labels. Instead, future football3 experiments must satisfy the new executable contract:
- exact same baseline/candidate prediction cutoff;
- canonical 0..6,7+ P(T) class order;
- shared `football3_core` implementations for LogLoss/Brier/RPS and paired bootstrap;
- temporal OOS only;
- explicit PIT timestamps/semantics;
- exact identity join and global-consumption audit;
- synthetic end-to-end pre-label smoke before any target access;
- `.T` attribute forbidden in scientific runners;
- sealed-pool guards;
- no same-label rescue/method shopping.

No sealed pool was opened by this audit. Formal scientific weights are unchanged.
