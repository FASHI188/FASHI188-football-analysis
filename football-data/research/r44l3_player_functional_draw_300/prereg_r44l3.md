# R44L3 球员功能结构 × 平局/一球胜负：冻结预注册

## 1. 身份与边界

- study_id: `r44l3_player_functional_draw_300`
- scope: `VIEWED_HISTORICAL_DEVELOPMENT / research only`
- formal_weight: `0`
- parent evidence: R1 与 R44L2 均保持不可改写的负证据；本研究不是对其调参或补丁。
- frozen background PR #176: 不执行、不修改、不合并。
- Provider/API-Football/付费API请求: `0`。

## 2. 科学问题

R44L2 已否定“真实阵型 + 逐场位置计数 + XI连续性”能稳定改善球队机会量预测。本轮不再重复位置计数，而直接检查一个信息类型不同的问题：

> 在严格只使用比赛之前已完成比赛的球员历史表现、历史市场价值记录、稳定球员画像以及当前历史首发身份的前提下，球员功能结构能否提高 **平局 vs 一球胜负** 的区分能力？

这是目前平局主线已验证的核心瓶颈；两球以上胜负不进入主标签。

## 3. 冻结数据源

Transfermarkt public dataset，沿用 R44L2 已使用的上游：`dcaribou/transfermarkt-datasets`，schema pin `154367dfa6d6eb0b86332e332f9df0a080c7ddce`。

运行时只下载公开静态文件并记录字节 SHA-256：
- `games.csv.gz`
- `game_lineups.csv.gz`
- `appearances.csv.gz`
- `players.csv.gz`
- `player_valuations.csv.gz`

不新增任何 Provider，不调用足球付费 API。

## 4. PIT 边界

- 当前比赛首发/阵型历史记录没有可证明的赛前 `available_at`，因此本研究 **不是正式PIT**。
- 当前比赛结果、当前比赛 appearance 表现、当前比赛进球/助攻/分钟、未来估值一律不得进入该场特征。
- `appearances` 只允许使用日期严格早于当前比赛的已完成比赛。
- `player_valuations` 只允许使用 `valuation_date <= match_date` 的最后一条历史记录；若时间粒度只有日期，则同日估值不作为严格时间优势证据，结果仍仅回顾性。
- `players` 只使用相对稳定画像：出生日期、惯用脚、身高；不使用“当前市值”作为历史比赛输入。

## 5. 冻结样本

- competition: English Premier League / `GB1`
- 训练支持：2023/24、2024/25及测试窗口之前的2025/26已完成比赛。
- 正式回顾测试窗口：2025/26按 `(date, game_id)` 排序的最后300场，与R44L2“latest 300”逻辑同类，但由Transfermarkt身份独立重建。
- 三个连续100场比赛块；每块只在块开始前的历史样本上拟合，块内不在线重拟合。
- 主标签只保留：90分钟平局 (`draw=1`) 与90分钟一球胜负 (`draw=0`)；净胜2球及以上不参与主分类评分。

## 6. 冻结基线

基线只用球队层、严格历史特征：
- 主客近5场 GF、GA、积分率；
- 主客近10场 GF、GA、积分率；
- 双方历史特征差与合计强度；
- 不使用当前比赛任何结果字段。

## 7. 新增球员功能特征族

只允许以下预注册族；不进行字段海选：

1. **近期可用性/比赛负荷**：当前XI球员过去5/10次出场的分钟、无近期分钟人数、稳定主力人数。
2. **攻击/创造职责**：当前XI基于过去10次出场的 goals/90、assists/90、G+A/90；前场与创造型位置单独聚合。
3. **防守中轴经验**：GK/CB/DM 当前XI过去比赛分钟聚合。
4. **核心缺失代理**：俱乐部赛前累计第一得分贡献者、第一助攻贡献者、前三G+A贡献者是否缺席当前XI；当前XI覆盖俱乐部既往G+A的比例。
5. **历史估值结构**：每名首发在比赛日之前最后可用历史估值；XI总量、均值、Top3集中度，以及当前XI相对俱乐部赛前最高11人估值的覆盖率。
6. **稳定画像**：XI平均年龄、年龄离散度、平均身高、左/右脚结构；仅作为小权重结构信息，不使用当前profile市值。

R44L2 已测试的 formation/position count/XI-overlap 不作为本轮新增核心，不允许事后用其救结果。

## 8. 冻结估计器

- 标准化 Logistic Regression，自实现确定性 L2 梯度下降；不做超参数搜索。
- L2 = `0.10`；迭代 `800`；固定学习率 `0.05`。
- baseline 与 baseline+player 使用同一训练样本和同一优化器。

## 9. 主指标与门

主指标：
- binary Log Loss（越低越好）
- Brier（越低越好）
- ROC-AUC（越高越好）

稳健性：
- 3个连续100场块至少2/3的 Log Loss 不恶化；
- 5000次 match-cluster bootstrap，seed=`20260812`，报告 `ΔLL = player - baseline` 的90%区间；
- 只有 pooled ΔLL < 0 且 bootstrap 90% upper < 0，才可称 `RETROSPECTIVE_SIGNAL_PASS`。

任何正结果最多是回顾性信号，不得晋级正式权重。

## 10. 禁止事后救援

运行后禁止：调L2、改fold、删球队、挑特征、改样本、改标签定义、根据结果选择某个字段子集、把两球以上胜负重新塞进主标签以改善分数。
