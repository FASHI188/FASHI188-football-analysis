# C072-N19 — closing-market-anchor + O/U2.5 movement replication1000

Project: **football3 only**.

Scientific lineage: C072-C → football3/N18 → N18C technical/scientific execution → N19. No C073–C077 scientific result is an input to model choice, feature choice, thresholds, or stopping rules.

## Evidence classification

This experiment is **REPLICATION / REPRODUCTION ONLY**. The 2025/26 public result domain is not claimed fresh, pristine, blind, or independent confirmation because labels in overlapping public football archives have been viewed elsewhere. `formal_weight=0` regardless of outcome.

## Question frozen before any N19 target read

After conditioning directly on the closing O/U2.5 market anchor, does the pre-match opening→closing O/U2.5 movement retain incremental information for the full total-goal distribution P(T=0,1,...)?

This is not a repair of N18C xG-state. N18C is terminal PARK. N19 uses no N18C candidate xG features.

## Source and zero-label identity lock

Source: Football-Data public 2025/26 CSV archive.

Fixed divisions: `E1,E2,E3,SC0,SC1,SC2,SC3`.

Identity-lock fields only: `Div,Date,HomeTeam,AwayTeam` plus `Avg>2.5,Avg<2.5,AvgC>2.5,AvgC<2.5`.

Zero-label selection rule: concatenate the fixed seven divisions, retain rows with valid identity and all four average O/U2.5 prices >1, parse date, sort by `(date, source_code, home_team, away_team, source_row_index)`, and freeze the first exactly **1000** rows. FTHG/FTAG/FTR are forbidden during identity lock.

The exact identity SHA256 is intentionally **PENDING ZERO-LABEL LOCK** in this first contract commit. No N19 score/result value may be read until a follow-up contract commit records the observed identity SHA and count=1000.

## Frozen market transforms

For each market stage `s ∈ {open, close}`:

`q_over_s = (1/O_s) / ((1/O_s) + (1/U_s))`.

`movement_logit = logit(q_over_close) - logit(q_over_open)`.

`mu_market` is the unique Poisson mean in `[0.05,8]` satisfying `P(T>=3 | mu_market)=q_over_close`.

No Pinnacle-specific field, 1X2, BTTS, Asian handicap, multi-line O/U ladder, manual draw/0-0/1-1 feature, or post-result field is allowed.

## Frozen models

Evaluation bins: `T=0,1,2,3,4,5,6,7+`. Both models use the same full-support NB2 count family and collapse only for scoring.

- **B0 closing anchor**: `log(mu_i)=log(mu_market_i)+beta0`.
- **C movement**: `log(mu_i)=log(mu_market_i)+beta0+gamma*z_i`, where `z_i` is movement_logit standardized using the training fold only.

Optimization: scipy L-BFGS-B on NB2 NLL; alpha bounded `[0.0001,3]`; beta0 unpenalized. Candidate uses fixed L2 `lambda=1.0` on `gamma` only. No alpha grid, C/grid search, transform search, optimizer search, feature search, division subset search, alternate window, or family search.

## Frozen chronological OOS

Exactly 1000 identities, sorted chronologically as locked.

- fold1: train 1–400, test 401–550
- fold2: train 1–550, test 551–700
- fold3: train 1–700, test 701–850
- fold4: train 1–850, test 851–1000

Pooled OOS = 600 matches.

## Metrics and gate

Primary: exact/collapsed 0..6,7+ multiclass LogLoss. Secondary: multiclass Brier and RPS. Top1/Top3 diagnostic only.

Paired match bootstrap: 5000 reps, seed `72019`, 90% CI for candidate-minus-baseline dLogLoss.

Replication PASS requires all:
- pooled dLogLoss < 0;
- bootstrap90 upper < 0;
- pooled dBrier <= 0;
- pooled dRPS <= 0;
- at least 3/4 chronological fold LogLoss wins;
- at least 4/7 source-division LogLoss wins among divisions represented in pooled OOS;
- probability conservation and numerical validity.

Strong replication screen (diagnostic, not confirmation): PASS plus dLogLoss <= -0.005, dRPS <= -0.0005, and 4/4 fold wins.

## Stopping rule and sealed boundaries

Result is accepted as-is. After labels open, no modification of movement transform, regularization, folds, divisions, sample ordering, NB2 family, alpha bound, PASS gate, or source subset is allowed on these 1000 labels.

C070-F Confirmation1597, N17 reserve266, N18 confirmation150, and every other sealed football3 pool remain unopened. This replication cannot authorize opening any of them.
