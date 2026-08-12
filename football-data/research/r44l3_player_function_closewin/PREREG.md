# R44L3 Player Function × Draw-vs-One-Goal-Win Screen

Status: VIEWED_HISTORICAL_DEVELOPMENT_DIAGNOSTIC_ONLY  
Formal weight: 0  
Promotion allowed: false

## Question
Use the already-built PR81 player/XI quality dataset to test the exact known bottleneck:
among matches that ended either as a draw or a one-goal win, do detailed player/XI features add stable separation beyond the same historical market baseline?

## Frozen inputs
- PR81 R2 Artifact ID: 8848368411
- Artifact ZIP SHA-256: 025bf7c11ca01dcda720977777af404079e26feb992bf6ed51cc27b5ba285946
- The Artifact's own manifest and Football-Data source hashes must verify exactly.
- No new football provider, paid API, secret, or new research data source.

## Existing player feature families
- absence: inferred regular absences and absent positions
- experience: prior-10 starts, minutes, low-history count
- attack: prior BPS/90 and xGI/90 of the actual historical XI
- defense: prior xGC/90 and defensive contribution of the actual historical XI
- goalkeeper: prior saves/90 and goals-conceded/90
- all_player: union of the above

Each player historical statistic was already generated with prior matches only in PR81.  
However, the historical XI itself is the actual historical starting XI and lacks proven pre-kickoff publication timestamps. Therefore this is conditional information-content research, not strict forward-PIT deployability.

## Target and evaluation
- Mechanism subset: final absolute goal difference <= 1.
- Positive class: draw.
- Negative class: one-goal home/away win.
- The final margin is used only to define this retrospective mechanism subset; it is not a deployable pre-match gate.
- Rolling targets: 2023/24, 2024/25, 2025/26.
- For every target season, training uses earlier seasons only.
- Market baseline and each player-family increment use the same fixed model.
- Models are fixed before execution; no hyperparameter search.
- Metrics: PR-AUC, ROC-AUC, Log Loss, Brier.
- 5,000 match bootstrap resamples, seed 20260812.
- Full per-feature univariate separation table is reported; no post-hoc feature deletion or hidden winner selection.

## Hard interpretation boundary
This run can only answer whether the existing detailed player/XI fields contain retrospective information about draw vs one-goal-win separation.
It cannot:
- promote a model;
- modify CURRENT, formal model/data/config;
- claim strict PIT;
- create current-match probabilities;
- create exact scores or EV;
- turn a viewed historical result into an independent confirmation.
