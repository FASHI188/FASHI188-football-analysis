# R42A — Dynamic Conditional Diagonal Tilt — Frozen Preregistration

Research-only retrospective experiment. Formal weight remains 0.

## Question
Does a strictly pre-match dynamic diagonal-dependence state add reproducible information inside `P(D|T)` after the existing direct-total / conditional-margin structure, without a standalone Draw classifier or any manual score bonus?

## Sample
- exactly 200 matches;
- SHA-256 identity selection with seed `42101`;
- latest complete season per competition;
- first reproduce and exclude R41A, R41B, R41C, R41D, R41E, R41F and the R41D replication sample: 1,400 identities total;
- required overlap with all prior consumed fixed200 samples: 0;
- labels are forbidden for sample selection.

## Frozen baseline
- direct `P(T=0..6,7+)` from the existing strictly-prior historical feature family;
- conditional `P(D|T)` from the same historical family;
- fixed logistic regularization `C=0.1` for all baseline models in this experiment;
- direct-total probabilities are identical between baseline and challenger.

## Frozen challenger
Only `T=2,4,6` receive a dynamic diagonal correction. `T=0` is already deterministic `D=0`; odd totals cannot draw; `T=7+` is not tilted because it mixes exact totals and parities.

For each eligible even total:

`logit(q_diag) = logit(p_base_diag) + beta0 + beta' Z`

where `Z` is frozen strictly-prior competition/team/H2H/balance/intensity state. The correction model is trained only on chronological out-of-fold baseline predictions from season-position blocks `0,1 -> 2` and `0,1,2 -> 3`.

The corrected `D=0` mass is set to `q_diag`. All non-diagonal states are rescaled proportionally, preserving their relative probabilities. No other mass movement is allowed.

Fixed tilt L2 penalty: `1.0`. No hyperparameter search.

## Primary gate
On the new fixed200, excluding `T=7+` from the primary conditional gate:
1. mean conditional-D LogLoss must improve;
2. paired 5,000-resample 90% LogLoss delta upper bound must be below 0;
3. conditional-D Brier must be non-worse;
4. conditional-D RPS must be non-worse;
5. downstream HDA LogLoss, Brier and RPS must each be non-worse.

Draw LogLoss, Draw AUC and Top-1 Draw precision/recall are diagnostics, not tuning targets.

## Hard boundaries
- fixed200 labels cannot choose features, C, L2, threshold or model form;
- no manual Draw probability, no fixed 1-1 bonus, no exact-score bonus;
- no provider request or new data collection;
- protected blind access 0;
- no current-match use;
- no formal model/data/config/CURRENT/main mutation.
