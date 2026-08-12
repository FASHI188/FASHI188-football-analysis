# 300场首发结构 × 预期机会量 — 真实运行审计 R1

## 1. 运行身份

- study_id: `lineup_opportunity_300_r1`
- branch at execution: `research/lineup-opportunity-300-r1`
- executed HEAD: `bd688e673ad07b9f65fc8ef985ad29d2c2d50814`
- GitHub Actions run: `31583239963`
- run status: `completed / success`
- zero-label-gate job: `94070993260` / `completed / success`
- model-oos job: `94071191738` / `completed / success`
- source commit: `8c97b2adb123863c3dd581e730f1360e89815ac2`
- formal_weight: `0`
- historical lineup PIT: `UNVERIFIED`
- scientific component promotion: `NOT_ALLOWED`

注意：workflow success只表示技术执行成功，不代表研究效果PASS。本文件以下科学裁决来自Artifact内实际输出。

## 2. Artifact身份

### Gate artifact

- artifact_id: `9136027911`
- digest: `sha256:b92d315ca3868e11a0b6539779b5605d46b7b90695f414b34474d54e6ae6eece`

### Model artifact

- artifact_id: `9136052161`
- digest: `sha256:73a03ae355e88ea4dc6e66e79495723ae46d4d548416aefa0baa86bae3f57270`

Model artifact内含：

- `gate/data_gate_r1.json`
- `model/model_result_r1.json`
- `model/predictions_r1.csv`
- `model/feature_rows_all_380_r1.csv`
- `model/run_summary_r1.md`

## 3. 零标签数据门

真实结果：`PASS`

- 全季fixture：380
- 唯一fixture：380
- finished=True：380
- 正式样本：最后300场
- team-match：600
- `fixture+element`重复key：10
- 冲突重复key：0
- 必需字段缺失：0
- 去重后恰好11名首发：600/600
- 恰好1名GK：600/600
- 首发位置完整：600/600
- anomaly_count：0
- gate阶段加载xG/xA标签：`false`

源文件SHA-256：

- fixtures.csv: `2d7e3950d346df14ca486cb09e9b9ba406d37d943775244eed06cdc021ffb3a9`
- merged_gw.csv: `0d09f1f1cb1b5520ec8e2f25238aa652efe2a263d8ca7cb2b6538b27bf86727d`

## 4. Primary：team xG OOS

### 三折

| Fold | B0 NLL | L1 NLL | ΔNLL=L1-B0 | 裁决 |
|---|---:|---:|---:|---|
| 1 | 0.28544602 | 0.32013455 | +0.03468853 | L1更差 |
| 2 | 0.21637496 | 0.21820151 | +0.00182656 | L1更差 |
| 3 | 0.17093713 | 0.18709366 | +0.01615653 | L1更差 |

**0/3 folds改善。**

### Pooled

- B0 NLL xG: `0.2242526999`
- L1 NLL xG: `0.2418099054`
- ΔNLL: `+0.0175572055`
- match-cluster bootstrap 10,000次、seed=20260812
- 90% CI: `[+0.0032075272, +0.0320401040]`

区间完整位于0以上，方向是L1稳定劣于B0，而不是“不显著的轻微改善”。

## 5. 点预测与校准

### team xG

- RMSE B0: `0.74978258`
- RMSE L1: `0.75331356`，相对恶化约 `+0.47%`
- MAE B0: `0.58142698`
- MAE L1: `0.59260565`，相对恶化约 `+1.92%`

校准：

- B0 intercept: `0.08637542`
- B0 slope: `1.03950888`
- L1 intercept: `0.46550246`
- L1 slope: `0.69589151`

L1点误差只小幅变差，但校准明显弱于B0；首发特征没有带来稳定概率质量增益。

## 6. Secondary：team xA

- pooled ΔNLL xA: `+0.0380764304`，L1更差
- RMSE B0/L1: `0.47246585 / 0.48178949`，约 `+1.97%`
- MAE B0/L1: `0.35847061 / 0.37079523`，约 `+3.44%`
- xA MAE超过预注册2%恶化阈值，因此该门FAIL。

三折ΔNLL xA：

- Fold1 `+0.09848512`
- Fold2 `+0.02132192`
- Fold3 `-0.00557775`

只有Fold3出现轻微xA改善，无法改变总体失败。

## 7. 球队驱动审计

删除任意一支球队后的 pooled ΔNLL xG 全部仍大于0，范围约：

- 最低：删除 Crystal Palace 后 `+0.01242187`
- 最高：删除 Bournemouth 后 `+0.01987344`

因此失败不是由单一球队异常造成。

局部存在改善球队，例如 Bournemouth、Leeds、Man City、Arsenal、Nott'm Forest、Liverpool；但正向改善贡献最大的单队 Bournemouth 仅占全部正向改善贡献约 `26.78%`，通过“单队集中度<50%”门。局部改善不足以形成总体可泛化增量。

## 8. 预注册硬门

- pooled_delta_negative: `FAIL`
- bootstrap_upper_negative: `FAIL`
- fold_improvement_at_least_2_of_3: `FAIL`
- xg_rmse_no_material_worse: `PASS`
- xg_mae_no_material_worse: `PASS`
- xa_rmse_no_material_worse: `PASS`
- xa_mae_no_material_worse: `FAIL`
- leave_one_team_out_all_negative: `FAIL`
- single_team_improvement_share_below_50pct: `PASS`

## 9. 正式裁决

**`RETROSPECTIVE_SIGNAL_FAIL`**

本R1不支持以下说法：

- “首发结构已经能稳定提高机会量预测”；
- “首发XI历史xG/xA能作为正式增量组件”；
- “首发模块已解决总进球或平局问题”；
- “可以获得非零正式权重”。

当前允许的准确表述：

> 在2025-26 EPL最后300场、严格时间顺序三折OOS、固定B0与L1、固定450分钟shrinkage、Ridge alpha=10、match-cluster bootstrap的预注册R1中，加入FPL位置壳、首发连续性和首发球员严格历史xG/xA/xGI特征后，team xG predictive NLL在3/3 folds均劣于基线，pooled ΔNLL=+0.017557，90% bootstrap区间[+0.003208,+0.032040]，因此回顾性信号门失败。

## 10. 解释边界

已验证事实：当前**这一套特征定义与模型组合**没有形成稳定增量。

尚不能据此证明：

- 所有形式的首发信息都没有价值；
- 真实战术formation/站位没有价值；
- 高质量赛前角色、压迫、推进、定位球职责等信息没有价值；
- 更严格forward PIT数据一定没有价值。

FPL的DEF/MID/FWD是粗位置注册壳，不是实际阵型；R1结果只能否定本预注册构造，不得扩大为“首发研究整体无效”。

后续若继续，必须把新的研究假设写成R2并保持R1原结果不变；不得在R1内赛后改特征、改alpha或挑fold救结果。
