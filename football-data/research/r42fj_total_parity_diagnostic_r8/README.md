# R42F/J Total-Parity Diagnostic R8

## Frozen question

On the exact same already-VIEWED 1,584 identities used by the Direct-T oracle/routing work, test whether the existing strict-pre-match historical response-state blocks add discrimination for **exact 90-minute total-goal parity**:

- target = `1` when exact `total_goals % 2 == 0`, otherwise `0`;
- baseline family = `core47`;
- challengers = `core47 + R42F18`, `core47 + R42J18`, and `all83`;
- primary comparison = `all83` minus `core47`.

The target uses the exact total-goal count, not the capped Direct-T `7+` bucket.

## Frozen model contract

Every family uses the existing `make_model` preprocessing shell (policy-independent median imputation + standardization + logistic regression) with fixed `C=0.01`. Fit rows are chronological `train + policy` rows satisfying the established common R42F/R42J coverage contract. There is no hyperparameter search, threshold search, market input, competition identity feature, or class weighting.

## Frozen development signal

A descriptive signal for considering a separate future OOS design requires all of the following on the VIEWED 1,584 rows:

1. `core47_logloss - all83_logloss >= 0.003`;
2. competition-season cluster bootstrap p95 for `all83 - core47` logloss is below zero;
3. `AUC(all83) - AUC(core47) >= 0.01`;
4. all83 Brier score is non-worse than core47.

This flag is **not** a scientific PASS and is **not** confirmation.

## Boundary

- retrospective / VIEWED mechanism diagnostic only;
- no B05+ labels opened;
- no new target-label access;
- no Provider or paid API requests;
- no Direct-T/HDA/score/selector mutation;
- no formal model/data/config/CURRENT/main mutation;
- `formal_weight=0`.

Only the frozen Actions artifact may be interpreted. Any future OOS use requires a separately frozen contract and explicit authorization before opening new labels.
