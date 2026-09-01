# Football3 V2.1 Validation Preregistration and Stop Gate

Status: FROZEN_BEFORE_IMPLEMENTATION
Frozen before reading any V2.1 result.

## Data roles
- Development: sealed 2022/23 development rows only (expected n=1826). May be used for design diagnostics, strict-time outer CV and parameter selection.
- Post-view diagnostic: already-unsealed 2023/24-2025/26 block (expected n=5256). May be scored once after development selection is frozen. It is POST_VIEW_DIAGNOSTIC only, never blind, confirmation or prospective.
- Frozen V1 and old V2 are read-only comparators.

## Phase-1 model family
Exactly INDEPENDENT_POISSON_FROZEN. Optional team venue-bias layer is fixed DISABLED for this phase (all team venue deviations exactly 0). No DC/NB/diagonal/Mar-Co/player/lineup/coach/market additions.

## Development-only parameter grid
All combinations are evaluated without access to post-view labels:
- team_half_life_days: [120, 240, 480]
- competition_half_life_days: [540, 900]
- team_prior_matches: [6, 12, 24]
- competition_prior_matches: [24, 48]
- residual_strength: [0.35, 0.60, 0.85]
- cross_season_shrink: [0.40, 0.65]
Fixed: global_home_rate=1.38, global_away_rate=1.12, min_rate=0.08, max_rate=6.0, max_goals=14.

Selection ordering is deterministic: lowest pooled outer-fold LogLoss; tie within 1e-12 -> lowest Brier; then lowest RPS; then lexicographically smallest parameter tuple. No post-view result may alter this selection.

## Strict-time 8-fold outer validation
Sort unique kickoff/cutoff batches chronologically. Split into 8 contiguous outer test blocks after an expanding warm-up of at least 20% of batches. For fold k, fit/replay state using only labels released before each test cutoff; no random split and no test-label state update before its result_available_at. Fold metrics are computed from predictions frozen before corresponding labels.

V1 comparison in development must use the immutable frozen V1 reference Artifact 9732754224 (digest sha256:5f0af0c428f19492715669c8e4fb2451ee94bf373f17f5560ff1a42114375bcb), its pure_engine.py bytes, and the sealed v1_lock.json bound to the exact same development SHA. The comparator is replayed under the same delayed-result PIT schedule; no synthetic or refit V1 is allowed.

## Metrics
For every comparator/model: 1X2 LogLoss, Brier, ordinal RPS, Top1, predicted mean home/away goals and actual mean home/away goals. Competition×season predicted/actual home-away direction is audited. Matrix invariants, PIT identity, batch and serialization guards are mandatory.

## Continue-research gate
V2.1 may receive V2_1_BASE_REPAIR_ENGINEERING_PASS_POSTVIEW_ONLY only if ALL are true:
1. every deterministic home/away invariant PASS;
2. every major competition×season group preserves a reasonable home direction (predicted mean home goals > predicted mean away goals when the group's actual home mean > away mean; no systematic reverse direction accepted);
3. development outer CV has >=6/8 folds with V2.1 LogLoss <= V1 and median(V1 LogLoss - V2.1 LogLoss) > 0;
4. pooled development LogLoss improves over V1 by >=0.001;
5. pooled development Brier and RPS do not worsen (tolerance 1e-12);
6. pooled development Top1 delta >= -0.0015;
7. one-shot post-view 5256 LogLoss, Brier and RPS are each <= V1 (tolerance 1e-12);
8. post-view absolute error of predicted home mean and away mean is no worse than V1 on each side;
9. all PIT, identity, duplicate/batch, source-boundary, exact-HEAD, file-scope, Artifact and remote-readback guards PASS.

Any failure => V2_1_BASE_REPAIR_REJECTED.
All-pass => V2_1_BASE_REPAIR_ENGINEERING_PASS_POSTVIEW_ONLY.
Never emit MODEL_CANDIDATE_PASSED, SCIENTIFIC_PASS or formal enablement. No new confirmation cohort or label unsealing without separate user authorization.