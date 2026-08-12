# 300场首发结构 × 预期机会量研究 — 预注册 R1

## 0. 身份与治理

- study_id: `lineup_opportunity_300_r1`
- branch: `research/lineup-opportunity-300-r1`
- status: `RETROSPECTIVE_STRUCTURE_SIGNAL_ONLY`
- formal_weight: `0`
- PIT status: `UNVERIFIED_FOR_HISTORICAL_LINEUPS`
- formal runtime/main: **不修改**
- 与 PR #176: **完全隔离，不混样本、不混权重、不混结论**
- 受 CURRENT V5.2.0 约束：历史动态首发若无可证明的赛前 `available_at`，不得获得 `SCIENTIFIC_COMPONENT_PASS` 或正式晋级。

## 1. 研究问题

在严格时间顺序评估下，已知当场首发 XI 后，首发结构与首发球员的赛前历史攻击质量，是否能在球队/对手历史强度与主客场基线之外，稳定改善对球队当场机会质量总量的预测？

这里的 estimand 与旧平局/HDA 首发研究不同：目标不是平局或赛果，而是球队当场机会质量总量。

## 2. 数据源冻结

- source: `vaastav/Fantasy-Premier-League`
- source commit: `8c97b2adb123863c3dd581e730f1360e89815ac2`
- season: `2025-26 EPL`
- fixture source: `data/2025-26/fixtures.csv`
- player-match source: `data/2025-26/gws/gw*.csv`
- source字段：`fixture`, `kickoff_time`, `team`, `position`, `element`, `was_home`, `starts`, `expected_goals`, `expected_assists` 等。

历史文件可用于回顾性发现，但缺少逐条首发原始赛前发布时间戳，因此 PIT 身份固定为未验证。

## 3. 样本冻结

- fixtures.csv 共 380 场，均为 finished=True。
- 按 `kickoff_time, fixture_id` 升序排序，固定取最后 300 场。
- 冻结区间：2025-10-24 至 2026-05-24。
- 该300场恰好落在 GW9-GW38，但**选样算法以 kickoff_time 为准，不以每GW固定10场为假设**；双赛周/空赛周不做人工修正。
- 统计单元：team-match，共 600 行。
- 前80场（GW1-GW8）仅允许作为历史 warm-up / lag 特征来源，不进入300场正式评分样本。

## 4. 零标签数据门

在查看正式300场效果量前，先执行：

1. `fixture + element` 去重；完全重复记录只保留一条。
2. 每个 fixture 必须恰有两队。
3. 每个 team-match 必须恰有 11 个 `starts=1`。
4. 11名首发中必须恰有 1 名 FPL `GK`。
5. 300场任一比赛两侧首发结构无法恢复时，标记 `DATA_GATE_FAIL`；不得按赛果/xG选择性替换比赛。
6. 先冻结缺失率、重复率、首发完整率、位置字段完整率，再开放标签。

## 5. 首发结构定义

### 5.1 允许的当前比赛预测侧信息

仅使用首发公布时原则上可知的：

- 当前首发 XI 身份；
- FPL 位置分类（GK/DEF/MID/FWD）；
- 主客场；
- 由**此前比赛**构造的球员/球队/对手历史特征。

### 5.2 明确禁止的当前比赛预测输入

- 当场 minutes；
- 当场 goals / assists；
- 当场 xG / xA / xGI；
- 当场 ICT/threat/creativity/influence；
- 当场比分、红牌、换人、VAR、赛中事件；
- 任何由终场信息反推的首发权重。

### 5.3 结构特征块（预注册，不做赛后特征搜索）

A. `role_shell`
- 首发 DEF/MID/FWD 数量。
- 仅称为 **FPL positional shell**，不得解释为真实战术 formation。

B. `xi_continuity`
- 当前 XI 中上一场也首发的人数；
- 当前 XI 中过去5场首发频率位于本队前11的人数。

C. `xi_prior_attack_quality`
- 每名首发仅用此前比赛形成的 shrinkage xG/90、xA/90、xGI/90；
- XI 均值、总和、Top-3 share、标准差；
- 低历史样本球员数（此前 <270 分钟）。

D. `role_attack_quality`
- DEF/MID/FWD 各位置首发的历史 xGI/90 汇总；
- FWD 是否为0/1/2+；
- Top-3历史攻击质量是否集中在同一位置组。

E. `team_context`
- home；
- 本队此前5场滚动 xG；
- 对手此前5场滚动 xG conceded（只能由此前比赛标签构造）；
- 本队/对手赛季截至赛前的 expanding xG 均值。

不使用教练主观标签、人工“核心球员”名单或赛后补权重。

## 6. 目标变量

### Primary

- `team_xG = sum(player expected_goals)`，对该 team-match 所有出场球员求和；这是标签，不进入同场预测侧。

### Secondary

- `team_xA = sum(player expected_assists)`。

### 暂不可用

- 纯机会次数（shots）与射正次数（SOT）：当前FPL GW源没有这两个字段，因此 R1 不人工用 threat/ICT 代替。若后续加入第二来源，必须先做独立来源与PIT审计再扩展。

## 7. 基线与候选模型

### Baseline B0

- home
- team prior 5-match xG
- opponent prior 5-match xG conceded
- team expanding prior xG
- opponent expanding prior xG conceded

### Candidate L1

- B0 + 5.3 中预注册的首发结构/历史首发质量特征。

首轮只比较 B0 vs L1；不做模型动物园，不因结果不理想临时增加交互项、深度模型或挑特征。

## 8. 时间顺序 OOS

- 300场按 kickoff 排序后切为 3 个连续 test folds，每 fold 100 场。
- Fold 1：前80场 warm-up/训练历史 -> 测试正式样本第1-100场。
- Fold 2：训练截至正式样本第100场 -> 测试第101-200场。
- Fold 3：训练截至正式样本第200场 -> 测试第201-300场。
- 同一比赛的两个 team-match 必须始终处于同一 fold。
- 所有 rolling / expanding / shrinkage 参数只能在当时训练窗口内拟合。

## 9. 评分与统计门

Primary score：`log1p(team_xG)` 的 Gaussian predictive NLL；均值模型与残差尺度均只能用训练窗口拟合。

Secondary：RMSE、MAE、校准斜率/截距；team_xA 做独立 secondary target 审计。

比较量：`Δ = score(L1) - score(B0)`，负数为改善。

晋级样式门（仅用于研究裁决，不等于正式晋级）：

- match-cluster bootstrap（两队同一match共同重采样）；
- pooled Δ 的 90% bootstrap upper bound < 0；
- 至少 2/3 folds 改善；
- secondary 不得出现预注册的实质恶化；
- 缺任一审计时不得写“通过”。

即便以上全部满足，由于历史首发 `available_at` 未验证，本研究仍只能输出 `RETROSPECTIVE_SIGNAL_PASS`，不能输出 `SCIENTIFIC_COMPONENT_PASS` 或正式权重。

## 10. 失效与反证

以下任一成立即否定/降级本路线：

- 首发完整性未通过零标签数据门；
- L1 pooled NLL 无稳定改善；
- 改善只由单一fold/单一强队驱动；
- 球员历史质量特征在升降级/转会样本中失真；
- 位置 shell 被误当真实 formation；
- 当前首发历史文件被证明含赛后修订且无法冻结；
- forward PIT confirmation 无法复现回顾性效果。

## 11. 当前结论权限

本文件是预注册，不包含300场效果结论。

在 300 场零标签覆盖门、真实 OOS 运行和审计完成前：

- 首发结构方向：`未启用`
- 对预期机会量的增量预测价值：`不可用`
- 正式模型权重：`0`
- 对单场赛果/总进球/比分：`不得产生任何直接影响`
