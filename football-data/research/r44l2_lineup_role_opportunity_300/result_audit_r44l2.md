# R44L2 300场真实阵型/逐场位置 × 机会质量 — 最终结果审计

## 身份

- study_id: `r44l2_lineup_role_opportunity_300`
- research branch: `research/r44l2-lineup-role-opportunity-300`
- frozen model workflow run: `31595573285`
- executed HEAD: `cb15abe88ab6a359143f297d36e1066714a0d37d`
- workflow job: `94110122955`
- workflow conclusion: `completed / success`
- model Artifact ID: `9140896160`
- Artifact SHA-256: `fc3541f099c1391da0761285bda87070291427ea81e570008a07c35572a888e4`
- model freeze SHA-256 observed in run: `3294e728a87d9e032ffc6b473555539b43e7475801e2077b2afd4cd628d0bd2a`
- formal_weight: `0`
- pit_eligible: `false`
- historical lineup available_at: `UNVERIFIED`

## 前置门

零标签正式300场门：`PASS_R44L2_ZERO_LABEL_GATE`

- 300/300 FPL fixtures
- 300/300 Transfermarkt match identity
- 600/600 team-match rows
- every team-match exactly 11 unique starters
- every starter match-specific position non-empty
- every team-match formation non-empty
- no missing/ambiguous identity
- no conflicting starter duplicates

训练支撑380场门：`PASS_R44L2_TRAINING_SUPPORT_380`

- 380/380 matches
- 760/760 team-match rows
- source hashes equal formal 300 gate
- all team-match structural inputs usable

## 冻结模型

Baseline B0 features (5):

- home
- team_roll5_xg
- opp_roll5_xgc
- team_exp_xg
- opp_exp_xgc

R44L2 candidate adds 21 structural features, for 26 total:

- match-specific counts for Centre-Back, Left-Back, Right-Back, Defensive Midfield, Central Midfield, Attacking Midfield, Left/Right Midfield, Left/Right Winger, Centre-Forward, Second Striker
- formation line count and four formation-line numeric slots
- previous-XI overlap
- recent-5 top-11 overlap
- formation_changed
- role_l1_change

Model: standardized Ridge, alpha=10.0. Formal sample is the frozen last 300 fixtures, split into three chronological 100-match OOS folds. Bootstrap is 10,000 match-cluster resamples with seed 20260812.

## 主结果：team xG

Pooled:

- B0 NLL: `0.22425269987027927`
- R44L2 NLL: `0.2673888051708597`
- ΔNLL (R44L2 - B0): `+0.04313610530058047`

Positive ΔNLL means the structural candidate is worse.

Fold deltas:

1. Fold 1: `+0.07817268656787325`
2. Fold 2: `+0.04538092733788943`
3. Fold 3: `+0.005854701995978729`

Improved folds: `0/3`.

Bootstrap match-cluster ΔNLL:

- mean: `+0.04310381616383069`
- p05: `+0.023258727719036025`
- p95: `+0.06369725185650359`

The entire preregistered 90% interval is above zero.

Point metrics:

- xG RMSE B0: `0.7497825818942667`
- xG RMSE R44L2: `0.7780617077163459`
- xG MAE B0: `0.5814269818228752`
- xG MAE R44L2: `0.6101138742280312`

Calibration:

- B0 intercept/slope: `0.08637541580581075 / 1.039508875268133`
- R44L2 intercept/slope: `0.632822556175259 / 0.6219209179776092`

Thus the structural candidate worsens both point error and calibration for team xG.

## 次结果：team xA

- pooled B0 NLL: `-0.038990525432166724`
- pooled R44L2 NLL: `-0.024680653411952188`
- ΔNLL: `+0.014309872020214533`
- xA RMSE: `0.4724658526916336 -> 0.47877059519988896`
- xA MAE: `0.35847061423757454 -> 0.3635290458775227`

Only fold 3 improves xA NLL; pooled result remains worse.

## 稳健性

Leave-one-team-out pooled xG ΔNLL remains positive for all 20 teams, ranging approximately from `+0.03864` to `+0.04716`.

The maximum positive-improvement contribution share is `0.6390452420946547` (Everton), failing the <50% concentration gate.

## Gate结果

- pooled_delta_negative: FAIL
- bootstrap_upper_negative: FAIL
- fold_improvement_at_least_2_of_3: FAIL
- xg_rmse_no_material_worse: FAIL
- xg_mae_no_material_worse: FAIL
- xa_rmse_no_material_worse: PASS
- xa_mae_no_material_worse: PASS
- leave_one_team_out_all_negative: FAIL
- single_team_improvement_share_below_50pct: FAIL

## 最终裁决

`RETROSPECTIVE_SIGNAL_FAIL`

准确解释：在本次冻结的300场、三折时间顺序OOS设计下，使用Transfermarkt真实阵型与逐场更细位置结构、首发连续性和结构变化特征，未能在B0之上形成可泛化的team xG增量，反而明显恶化NLL、RMSE、MAE和校准。

该结果不能扩大为“所有首发/战术信息都无价值”。它只否定本轮具体表示：阵型标签 + 逐场位置计数 + XI连续性/结构变化的这组冻结特征。

由于历史首发 `available_at` 仍不可证明为赛前可见，本研究始终只属于回顾性研究；无论数值如何均不得获得正式权重。本次实际结果同时为负，因此 `formal_weight=0` 保持不变。
