# Football3 New Engine V2 — Dynamic Squad, Tactical Regime, Joint Score and Match Process Engine

Status: PREREGISTERED_BEFORE_IMPLEMENTATION
Anchor: `7c1815c47102412e88f72189e2b8f837d9b73a42`
Branch: `football3/new-engine-v2-joint-score`

## Purpose
Build a new independent football probability engine. V2 is not a continuation, wrapper, refit, or renamed version of V1/V500/R43. V1 and the frozen old model are comparison baselines only.

## State machine
`CONTRACT_FROZEN -> CORE_BUILD -> OUTER_FOLD_RESEARCH -> LAYER_ABLATION -> FINAL_RULE_FROZEN -> BLIND_FINAL_PREDICTED -> FINAL_SCORED -> {MODEL_CANDIDATE_PASSED|NOT_PROMOTED}`. Only `MODEL_CANDIDATE_PASSED` may transition to `FORWARD_PREREGISTERED -> FORWARD_30 -> FORWARD_100 -> FORWARD_300`. Any governance violation transitions to `STOPPED_GOVERNANCE`. Missing lawful data transitions only the affected layer to `BLOCKED_DATA` and does not imply success.

## Pure engine outputs
For every eligible prematch fixture: full normalized joint score matrix; matrix-derived 1X2; independent 1X2 head when the dual-head candidate is active; minimum-KL reconciliation; uncertainty; cold-start bucket; retained-layer receipts. Pure output must not consume odds, market probabilities, closing prices, legacy predictions, or provider-secret fields.

## Core modules
1. Dynamic hierarchical team attack/defence and home/away state.
2. Competition-specific negative-binomial goal marginals with shrinkage.
3. Joint-score candidate competition: independent Poisson, Dixon-Coles, diagonal inflation, NB+diagonal, NB+Mar-Co, NB+Sarmanov, and joint-score+independent-1X2 dual head.
4. Optional player/lineup, bench, coach/tactical regime, fitness, match-process layers only when lawful PIT evidence exists and ablation retains them.
5. Strict validation, blinded holdout scoring, audit/adversarial probes.

## Non-negotiable invariants
- Same kickoff/cutoff batch is predicted completely before any same-batch update.
- Every score label is a strict non-bool integer >=0; no coercive `int()` on external labels.
- All floating inputs/parameters are finite and range/relationship validated.
- Unknown schema fields, identity, time, provenance, or PIT status fail closed.
- Prediction and final-label scoring are separate processes and disk artifacts.
- Engineering green is not scientific promotion.
- No formal activation; terminal branch-local completion status may be `GPT_REBUILT_PENDING_CODEX_RECHECK` only after all authorized remote acceptance work is complete.

## Stop conditions
Stop on branch HEAD drift, unknown branch provenance, out-of-whitelist file, SHA/tree/blob/artifact mismatch, future information, label leakage, ambiguous identity, unverifiable known_at, critical test failure, need for force/merge/extra permission, old-model code entering V2 core, or inability to prove pure-market isolation.