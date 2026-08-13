# R40 PIT availability residual preregistration

Research only; formal_weight=0; formal model/data/config/CURRENT changes=0.

Status if executed: retrospective PIT-reconstructed rolling OOS exploratory evidence, NOT project-independent confirmation.

Source gate already passed at run 31726018581 / head 3ae8d20bca2668202acf949efc7a5bb14afbe9b8: 1738 eligible pre-match fixtures, 5 seasons each >=150, target labels read=0, manifest SHA256 c1ad9abb3dc91af82f3e4c39bad530d9ffff8a5901ad8040c6c4bb0c2f936c26.

## Frozen baseline
Football-Data E0 closing 1X2, priority AvgC -> B365C -> MaxC -> PSC. Convert to no-vig pH/pD/pA.

## One frozen candidate
R40_PIT_AVAILABILITY_ATTACK_BALANCE_RESIDUAL_R1.

logit(qD) = logit(market_pD) + X beta. Fit beta by fixed-offset logistic IRLS, L2 lambda=1.0, max_iter=50, tolerance=1e-8. Train-fold-only standardization. Preserve market H:A ratio inside non-draw mass 1-qD.

Fixed X only:
1. mean home/away regular-risk share;
2. absolute difference regular-risk share;
3. mean home/away attacking-BPS-at-risk share;
4. absolute difference attacking-BPS-at-risk share;
5. sum log1p available attacking BPS;
6. absolute difference log1p available attacking BPS.

regular-risk share = regular_risk_count/max(regular_count,1).
attacking-BPS-at-risk share = attack_bps_at_risk/max(attack_bps_at_risk+attack_bps_available,1e-9).

Forbidden candidate fields: ep_this, ep_next, xP. No candidate search or feature change after labels open.

## Frozen rolling OOS
F1 train 2020-21 -> test 2021-22.
F2 train 2020-21..2021-22 -> test 2022-23.
F3 train 2020-21..2022-23 -> test 2023-24.
F4 train 2020-21..2023-24 -> test 2024-25.
Each test fold requires >=150 market-matched rows.

Metrics: HDA LogLoss, Draw LogLoss, Draw Brier, Draw AUC, HDA accuracy, natural Top-1 Draw count/hits. No forced draw selector and no threshold search.

Pooled diagnostic: 5000 calendar-date bootstrap, seed 20260814, 90% CI for HDA LogLoss delta.

Exploratory survival gate: pooled HDA LL<market; pooled Draw LL<market; pooled Brier<market; pooled Draw AUC>market; bootstrap90 HDA-LL upper<0; at least 3/4 test folds non-worse in HDA LL; no single fold HDA LL deterioration >0.005.

Even PASS cannot authorize formal promotion. R34-R39 samples/selectors remain sealed and untouched.
