# Football3 R44 Historical 20k Research Line

Status: ACTIVE RESEARCH
Base HEAD: ee98423a99fd9c8947a1aa339eadaa3e4b2f2b51
Branch: football3/r44-historical-20k-research

## Purpose
M11 prospective 300 is parked as a final validation line and must not block research throughput. R44 uses historical data only for strict time-ordered research, tuning and candidate selection.

## Hard constraints
- 90-minute 1X2 target only.
- No future leakage: every feature, lineup/availability proxy, odds snapshot and calibration input must exist before the prediction timestamp.
- No random train/test split.
- Strict chronological / walk-forward OOS evaluation.
- No use of final score or post-match data in feature construction.
- Historical research cannot be inserted into M11 fresh-forward cohort.
- Any candidate promoted from R44 must be frozen as a new prospective version; M11 stays unchanged.

## First research tranche
Use an exact frozen 20k historical sample drawn only from already-governed historical sources. Preserve source hashes and row identities.

Evaluate, at minimum:
1. Existing frozen baseline(s).
2. Draw-focused residual / calibration component.
3. League-aware calibration.
4. Dynamic team strength and recency sensitivity.
5. Market-anchor fusion weight sensitivity.
6. Scoreline-family changes relevant to 0-0 / 1-1 mass.

## Required metrics
- Multiclass log loss
- Brier score
- RPS
- Top-1 accuracy (secondary, not sole optimization target)
- Draw recall / precision / calibration
- Reliability by predicted-probability bins
- League and time-slice stability

## Promotion gate
A candidate may be promoted only if it improves the preregistered primary probabilistic metric(s) on strict OOS without material degradation in calibration/stability, and survives a later untouched chronological holdout. No candidate is allowed to modify M11.
