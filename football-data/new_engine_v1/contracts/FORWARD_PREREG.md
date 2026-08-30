# Football3 New Engine V1 prospective zero-label protocol

Status: preregistered prospective evidence protocol only. It does not activate, merge, replace, or modify any formal model.

## Frozen candidate

The scientific candidate is exactly commit `7986b5b528338d1d359f1287677f4ab92e453f39` and the Run 33295213300 artifact with digest `sha256:8dc51ac1f9b9352b2a61bbdf7d3ffa46ca2aa6c13cc390af783f5df2112ac3c3`. The pure-model source hash and selected hyperparameters are frozen in `forward/model_lock.json`. Prospective collection may not alter them.

The additional `historical_gate_reaudit.py` is audit-only. Before the first prospective row is accepted, it must reproduce the original blind prediction and scored-label hashes exactly and recalculate the preregistered underdog threshold on the same-match intersection. If that corrected gate is not `MODEL_CANDIDATE_PASSED`, prospective enrollment is forbidden.

## Prospective refit rule

After hyperparameter selection and the historical OOS decision are frozen, the deployment state may be rebuilt once from all 20,746 already-ended matches in the frozen historical universe through 2026-05-24. This uses the same fixed New Engine V1 algorithm and parameters. It is not another model-selection step. No prospective result can enter that bootstrap or any later update in this 30/100/300 zero-label registry.

## Enrollment

- Source: the already-existing public Kambi/BetCity capture utility inherited from the M10 anchor; no paid provider, added API, or user secret.
- Identity: exact current-team registry/registered alias only; no fuzzy matching.
- Eligible event state: `NOT_STARTED`.
- Observation time: at or after `forward_not_before_utc` and no later than kickoff minus 60 minutes.
- Pure prediction input: only a safe identity/time projection. Prices, odds, AH, OU and market probabilities are stripped before the pure engine is called.
- One provider event can be enrolled once. Any identity drift fails closed.
- Ledger target: 300 rows maximum.
- The ledger contains predictions and uncertainty but no realized result, score, settlement or outcome label.

## Checkpoints

- 30 rows: operational stability check only.
- 100 rows: trend observation only.
- 300 rows: stable prospective confirmation checkpoint.

These checkpoints do not by themselves modify the formal model. Outcome scoring is explicitly outside this zero-label recording workflow and requires a later separately authorized label-unseal procedure.

## Persistence and automation

The prospective ledger is persisted in immutable GitHub Actions artifacts, not committed to the branch. Re-runs of the same workflow run restore the previous attempt artifact and append only newly eligible rows while remaining bound to the exact same workflow HEAD. Checkpoint prefix hashes are immutable.

## Stop conditions

Immediately fail closed on source/identity ambiguity, HEAD/model hash drift, history-universe drift, prediction-time conflict, duplicate provider event identity conflict, label-like top-level ledger fields, market fields reaching the pure runner, corrected scientific gate failure, or any attempt to exceed 300 rows.
