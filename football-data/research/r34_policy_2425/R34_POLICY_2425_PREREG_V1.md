# R34 Policy 2024/25 — One-shot Preregistration V1

## 1. Status and scope

- Track: isolated pure 1X2 draw research.
- Registration status: research/challenge only.
- Formal weight: `0`.
- Settlement: 90 minutes including stoppage time.
- Production model, production configuration, formal datasets and the unique CURRENT rule file: unchanged.
- R20 and R23: unchanged.
- This document freezes one structural cross-season validation. It does not authorize tuning or promotion.

## 2. Research question

Can the already-defined R34.0 pre-match score-state transition structure add stable, cross-season 1X2 information beyond the direct pre-match market when applied to the independent English Premier League 2024/25 policy season?

The target is not an isolated accuracy increase. The primary target is lower out-of-sample proper scoring loss without degrading probability conservation or materially worsening Brier/RPS.

## 3. Frozen evidence source for the policy season

The goal-process evidence source is fixed before model scoring:

- Repository: `harryji168/email_solutions-sports`
- Path: `public/sports/leagues/ENG_PR/2024-2025.json`
- Commit: `0d9916f1c1c0679886cc5603ebd268dd31554d23`
- Expected Git blob SHA-1: `af481cd91dc3d6033d74dc7531fa398ca8653248`
- Expected competition: `ENG PR`
- Expected season size: 380 completed matches, 20 teams, 38 matches per team, 19 home and 19 away.

The source date and time fields are preserved verbatim. Because the source may encode a timezone different from the market files, linkage must use normalized team identity plus a fixed `±1 day` date tolerance. No silent date rewriting is allowed.

## 4. Hard data audit before any scoring

The policy-season chain is eligible for model scoring only if all checks pass:

1. Source bytes match the frozen Git blob SHA-1.
2. Exactly 380 match records and 380 unique match IDs.
3. Exactly 380 unique directed home-away fixtures.
4. Exactly 20 teams; every team has 38 appearances, 19 home and 19 away.
5. Every record is `FT` and belongs to `ENG PR`.
6. Every final and half-time score parses.
7. Every non-zero final score is reconstructed event-by-event from 0-0.
8. Each event increments exactly one side by one goal.
9. Event order is non-decreasing within football period semantics; first-half stoppage precedes second-half minutes, and second-half stoppage follows minute 90.
10. Parsed event count equals the sum of all final-score goals.
11. All 0-0 matches remain in the sample with an empty event chain.

Any failed hard audit stops the experiment. Missing or malformed matches may not be dropped selectively.

## 5. Frozen challenger structure

The single allowed challenger is the existing R34.0 discrete-time score-state hazard structure, without feature search or parameter tuning after viewing 2024/25 outcomes.

Frozen elements:

- Six 15-minute match intervals.
- Separate home and away interval goal counts.
- Same R34.0 pre-match market inputs.
- Same simulated score-state inputs.
- Same domain/team historical period-rate construction.
- Same-date freeze: all matches on a date are scored before any result from that date updates history.
- Poisson regression regularization `alpha = 1.0`.
- No threshold search, no alternative time buckets, no feature additions/deletions and no post-hoc calibration selected from 2024/25 results.

Cross-season protocol:

- Fit coefficients once using the eligible 2023/24 evidence only.
- Freeze coefficients before accessing 2024/25 labels.
- Score 2024/25 chronologically without coefficient refitting.
- Team/history state may update only after earlier policy-season matches have finished, under the same same-date freeze.
- 2024/25 outcomes may be used only for the final one-shot evaluation and audit.

## 6. Market linkage and coverage

The comparison baseline is the direct pre-match 1X2 market probability already used by R34.0.

- Market and policy records must join by home team, away team and the fixed date tolerance.
- Duplicate or ambiguous joins fail closed.
- Missing market rows are reported explicitly.
- No performance-based subset selection is permitted.
- Full matched coverage, exclusions and reasons must be included in the receipt.

## 7. Frozen evaluation

Primary metric:

- Mean multiclass LogLoss difference: `challenger - market`; negative is better.

Required uncertainty check:

- Match-level or date-cluster bootstrap with a predeclared deterministic seed.
- Report the 95% interval for the LogLoss difference.

Secondary metrics:

- Multiclass Brier score.
- Ranked Probability Score.
- Full 1X2 Top-1 accuracy.
- Draw probability mean and standard deviation.
- Draw ROC-AUC where defined.
- Number of draw Top-1 calls, draw precision and draw recall.
- Probability sum residual and invalid-probability count.

## 8. Decision rule

`CROSS_SEASON_PASS` requires all of the following:

1. Challenger mean LogLoss is lower than market.
2. The 95% bootstrap interval for `challenger - market` is entirely below zero.
3. Brier and RPS do not materially worsen.
4. Probability conservation and all execution audits pass.
5. Any draw Top-1 improvement is reported on the complete matched sample, not a post-hoc selected subset.

Otherwise the result is `CROSS_SEASON_FAIL_NO_PROMOTION`.

A pass is research evidence only and does not itself grant formal weight. A fail ends the pre-match route that relies only on market plus historical goal timing/score-state transitions. The next permitted research family is richer process evidence such as shots/xG, red cards, substitutions, VAR and score-state pressure.

## 9. Explicit prohibitions

- No 2026/27 collection.
- No API credential or paid-provider request.
- No tuning R34.0 using the former 2023/24 test block or the 2024/25 policy labels.
- No claiming convergence as predictive success.
- No modification of R20, R23, production model, production configuration, formal data registry or CURRENT.
- No promotion, EV, betting recommendation or formal probability output from this research run.
