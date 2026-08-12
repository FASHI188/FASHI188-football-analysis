# 300场首发结构 × 预期机会量 — 执行冻结 R1

本文件在300场正式效果标签开放前建立，用于关闭原预注册中尚未写死的算法自由度。它只补足执行细节，不改变研究问题、样本、目标变量或正式权重。

## 1. 不变身份

- study_id: `lineup_opportunity_300_r1`
- branch: `research/lineup-opportunity-300-r1`
- formal_weight: `0`
- historical lineup PIT: `UNVERIFIED`
- maximum possible verdict: `RETROSPECTIVE_SIGNAL_PASS`
- 禁止输出 `SCIENTIFIC_COMPONENT_PASS`、正式晋级或非零正式权重。

## 2. 样本与时间

- 2025-26 EPL 全季380场按 `(kickoff_time, fixture_id)` 升序。
- 最后300场为正式评分样本；前80场只作 warm-up / 训练历史。
- 正式测试固定为连续3折，每折100场。
- 同一比赛两队必须处于同一fold。
- **相同 kickoff_time 的所有比赛作为一个批次**：先统一生成赛前特征，再统一写入该批比赛结果，禁止同一开球时刻横向泄漏。

## 3. 零标签门

Gate job只读取身份/首发字段，不载入 `expected_goals` / `expected_assists`：

- `fixture`, `kickoff_time`, `team`, `position`, `element`, `was_home`, `starts`。
- 先按 `(fixture, element)` 去重。
- 允许完全重复行被确定性删除；同一 `(fixture, element)` 的身份/首发字段若互相冲突则直接 FAIL。
- 300场必须全部存在；每场恰有2队；每个team-match恰有11名 `starts=1`；首发中恰有1名GK；主客身份必须内部一致。
- 任一正式样本失败：`DATA_GATE_FAIL`，model job不得启动。

## 4. 历史首发质量 shrinkage

球员历史只使用严格早于当前 kickoff 的比赛。

固定伪样本分钟：`PRIOR_MINUTES = 450`。

对指标 m ∈ {xG, xA, xGI}：

`shrunk_m90 = 90 * (player_prior_m + position_prior_rate_per_min * 450) / (player_prior_minutes + 450)`

其中：

- `position_prior_rate_per_min = prior_position_total_m / prior_position_total_minutes`；
- position prior同样只能使用严格历史；
- 若该位置尚无历史分钟，则回退到严格历史的全位置 league rate；
- 若全局也无历史分钟，则rate=0；
- xGI固定为xG+xA，不另行调参。

转会球员的个人攻击质量历史跨球队保留；球队连续性/近5场首发频率只使用当前球队历史。

低样本球员固定定义：当前赛前累计历史分钟 `<270`。

## 5. 结构特征的精确定义

### Baseline B0

1. `home`
2. `team_roll5_xg`：本队此前最多5场xG均值
3. `opp_roll5_xgc`：对手此前最多5场xG conceded均值；xGC定义为该队过去对手的team_xG
4. `team_exp_xg`：本队赛季截至赛前的expanding xG均值
5. `opp_exp_xgc`：对手赛季截至赛前的expanding xG conceded均值

没有至少1场本队和对手历史的team-match不进入模型训练；300场正式测试按赛程位置应全部具备历史，否则记录为运行失败，不做结果导向替换。

### Candidate L1新增

A. FPL role shell：`DEF_count`, `MID_count`, `FWD_count`。

B. continuity：
- `prev_xi_overlap`：当前XI与本队上一场XI交集人数；
- `recent5_top11_overlap`：此前最多5场中按首发次数降序、element id升序打破并列后，前11名与当前XI交集人数。

C. XI prior attack quality：对 shrunk xG90 / xA90 / xGI90 各计算：
- sum
- mean
- std（population ddof=0）
- top3_share；分母<=0时固定为0。

另加：`low_history_count`。

D. role attack quality：
- DEF / MID / FWD 首发的 shrunk xGI90 各自总和；
- `fwd_zero`；
- `fwd_two_plus`；
- `top3_same_position`：按shrunk xGI90降序、element id升序取Top3，三人FPL position相同则1，否则0。

FPL position shell只表示注册位置结构，不得解释为真实战术formation。

## 6. 标签完整性

Model job在Gate PASS后才读取：`minutes`, `expected_goals`, `expected_assists`。

- 任一 `minutes>0` 球员的 xG 或 xA 缺失：`LABEL_INTEGRITY_FAIL`。
- `team_xG = sum(player expected_goals)`。
- `team_xA = sum(player expected_assists)`。
- xG/xA只用于当前比赛标签和之后比赛的历史特征，绝不进入同场预测输入。

## 7. 均值模型固定

B0与L1都使用：

- `StandardScaler`：仅fit训练窗口；
- `Ridge(alpha=10.0, fit_intercept=True)`；
- 不做alpha搜索、不做特征筛选、不加交互项、不换模型。

目标固定为 `log1p(team_xG)`；xA secondary使用完全同样流程预测 `log1p(team_xA)`。

每fold的Gaussian NLL残差尺度不由候选模型复杂度单独估计，而固定为该fold训练标签 `log1p(y)` 的样本标准差（ddof=1，最低0.05），B0与L1共享同一sigma，避免复杂模型用训练残差人为改变NLL尺度。

原尺度预测：`max(0, expm1(pred_log))`。

## 8. 评分固定

Primary：team-level `log1p(team_xG)` Gaussian NLL，`delta_nll = NLL_L1 - NLL_B0`，负值为L1改善。

Secondary：原xG尺度 RMSE、MAE、校准 slope/intercept；team_xA独立计算NLL、RMSE、MAE。

### Bootstrap

- cluster = fixture，两队作为同一cluster；
- bootstrap repetitions = `10000`；
- RNG seed = `20260812`；
- 每个replicate对300个fixture有放回抽样，统计fixture内两队平均delta再求总体均值；
- central 90% CI = 5th / 95th percentile；
- gate使用95th percentile作为 `90% bootstrap upper bound`。

## 9. 研究通过门固定

同时满足才可写 `RETROSPECTIVE_SIGNAL_PASS`：

1. pooled primary delta_nll < 0；
2. 90% bootstrap upper bound < 0；
3. 3个fold中至少2个fold的primary delta_nll < 0；
4. pooled xG RMSE_L1 <= 1.02 * RMSE_B0；
5. pooled xG MAE_L1 <= 1.02 * MAE_B0；
6. pooled xA RMSE_L1 <= 1.02 * RMSE_B0；
7. pooled xA MAE_L1 <= 1.02 * MAE_B0；
8. 单队驱动审计通过：删除任意一支球队的team-match后，primary pooled delta仍 <0；
9. 正向改善贡献中，单一球队占比不得 >=50%。

校准 slope/intercept作为审计指标完整报告，但R1不再新增事后阈值。

任一硬门失败：`RETROSPECTIVE_SIGNAL_FAIL`。

## 10. 固定随机性和环境

- model不使用随机训练算法；
- bootstrap seed固定为20260812；
- Python依赖版本由workflow日志与Artifact保存；
- 数据下载固定到source commit `8c97b2adb123863c3dd581e730f1360e89815ac2`；
- workflow只读contents，不访问Provider/API-Football，不使用Secret，不修改main，不提交模型资产。
