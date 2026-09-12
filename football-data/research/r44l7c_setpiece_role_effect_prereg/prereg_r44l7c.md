# R44L7C 定位球职责对平局识别的增量效果验证：冻结预注册

## 1. 当前状态

`PRE_REGISTERED_NOT_AUTHORIZED_NOT_RUN`

本文件只冻结未来一次效果验证的科学合同。创建本文件不构成读取目标赛果、拟合模型、评分、晋级或正式资产修改的授权。

上游 R44L7B 已在严格 prior-only、零目标标签条件下完成 837 场 / 1674 team-target 全量覆盖，终态 `PASS_R44L7B_FULL_PRIOR_ONLY_ZERO_LABEL_COVERAGE`。R44L7B exact HEAD=`5dd07c66634779609b4691520bc6904a56cd155a`，run=`31659501770`，job=`94321145557`，Artifact=`9165673664`，Artifact SHA-256=`f5c5bcb2250129efd19cf629149199d9c9c1a6f9faec792e5e7e046c3c97d4a6`。

## 2. 假设

唯一主要假设：在只使用目标比赛之前已结束比赛信息构成的球队基础状态之上，**当前首发是否保留近期实际角球/任意球职责，以及定位球执行职责的集中度/稳定度**，对 `平局 vs 一球胜负` 有可泛化的增量判别信息。

这不是“定位球导致平局”的因果假设，也不是直接预测精确比分。

## 3. 数据身份与永久排除

固定 source：`hudl/open-data@b0bc9f22dd77c206ddedc1d742893b3bbe64baec`

仅允许：
- Premier League 2015/16：competition_id=2, season_id=27；
- Serie A 2015/16：competition_id=12, season_id=27；
- Ligue 1 2015/16：competition_id=7, season_id=27。

`La Liga 2015/16 (competition_id=11, season_id=27)` 永久排除，因为本对话 schema 检查时其比赛容器结果字段已经被展示，状态固定为 `VIEWED_EXCLUDED_RESULT_FIELDS_DISPLAYED_DURING_SCHEMA_INSPECTION`。

## 4. 结算与主目标

只计算90分钟含补时，不含加时、点球或晋级。

主二分类目标：
- `y=1`：90分钟平局；
- `y=0`：90分钟一球胜负，即 `|主队进球-客队进球|=1`；
- 净胜两球及以上的比赛不进入主二分类效果样本。

不得根据打开后的标签改变主目标定义。

## 5. 基线模型

基线只使用严格赛前、prior-only 的简单球队状态，不使用市场，不使用当前比赛 event，不使用定位球职责字段。

每队固定 prior10 五项：
1. goals_for_per_match；
2. goals_against_per_match；
3. points_per_match；
4. goal_difference_per_match；
5. draw_rate。

比赛级同时加入：
- 主客侧身份；
- competition fixed effects。

同一比赛的结果只能在该比赛特征封存并完成之后，进入后续比赛的 prior10 历史。

## 6. 唯一候选增量

候选模型 = 完全相同基线 + 主客双方各5项 R44L7B 已冻结的定位球职责字段，共新增10项：

1. prior5_setpiece_passes；
2. prior5_unique_takers；
3. prior5_top1_share；
4. prior5_top1_taker_in_current_xi；
5. prior5_xi_role_retention。

不得增加交互项、主客差值手工特征、符号翻转、字段筛选、替代窗口或新职责定义。

## 7. 固定估计器

基线与候选必须使用完全相同算法：
- standardized L2 Logistic Regression；
- `C=1.0`；
- solver=`lbfgs`；
- max_iter=`2000`；
- class_weight=None；
- 不做超参数搜索；
- 不选择分类阈值作为主要评分依据。

主要比较 proper scores，而不是事后挑 Top-1 阈值。

## 8. 时间顺序 OOS

每个赛事域前100场是 warm-up，不进入837场正式效果容量。

837场 post-warmup 比赛按 `(match_date, kick_off, match_id)` 全局时间排序，并按相同时间戳不可拆分原则切为3个近似等量、连续 chronological OOS folds。

每个fold：
- 训练只能使用严格早于该fold起点的合格二分类样本；
- earlier completed folds 可在后续fold训练时进入历史；
- fold内部不得边预测边更新；
- baseline 与 candidate 必须使用完全相同训练身份。

任何同日/同时间比赛必须先统一生成预测，之后才允许其结果进入后续历史。

## 9. 开标签后的样本量硬门

用户未来若明确授权读取目标标签，必须**先计数、后拟合**。以下全部满足才允许任何模型拟合：

- 主二分类总样本 >= 450；
- 平局 >= 120；
- 每个赛事域主二分类 >= 120；
- 每个赛事域平局 >= 30；
- 每个3个正式OOS fold主二分类 >= 120。

任一失败固定终态：`STOP_R44L7C_UNDERPOWERED_AFTER_LABEL_OPEN`，且 `model_fits=0`。

不得像 R44L4 那样在发现样本不足后追加域、降低门槛或改变fold。

## 10. 指标与晋级门

主要指标：LogLoss、Brier。

次要指标：Draw AUC、校准截距、校准斜率。

Bootstrap：
- 10,000次；
- seed=20260813；
- match-cluster bootstrap；
- 90%区间。

R44L7C 研究信号 PASS 必须同时满足：
1. pooled `ΔLogLoss = candidate - baseline < 0`；
2. ΔLogLoss 的90% bootstrap上界 `< 0`；
3. pooled `ΔBrier < 0`；
4. 3个chronological folds中至少2个 `ΔLogLoss < 0`；
5. 3个赛事域中至少2个 `ΔLogLoss < 0`。

全部满足终态：`RETROSPECTIVE_SETPIECE_ROLE_INCREMENT_PASS_REQUIRES_INDEPENDENT_CONFIRMATION`。

任一科学门失败：`RETROSPECTIVE_SETPIECE_ROLE_SIGNAL_FAIL`。

即便 PASS，也只代表回顾性、时间顺序研究信号通过，`formal_weight=0`，仍必须有真正独立确认后才可能讨论晋级。

## 11. 当前禁止事项

在用户新的明确标签授权之前：
- target labels accessed=0；
- model fits=0；
- candidate probabilities=0；
- 不得创建或运行标签执行workflow；
- 不得读取目标比分来做样本筛选、fold调整、字段选择或调参；
- 不得把 workflow success 写成科学 PASS；
- 不得修改正式模型、正式数据、config 或 CURRENT；
- 不得给该研究非零正式权重。

未来获得一次明确授权后，也禁止：换 prior5/prior10 窗口、换 C、删联赛、加入 La Liga 2015/16、增加字段、挑fold、事后反转变量或重跑失败确认。
