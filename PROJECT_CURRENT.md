# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 46
- updated_at_utc: 2026-08-14T01:19:00Z
- updated_by: GPT-5.6 Sol
- status_source: `R41_PIT_AVAILABILITY_CHANGE_SPARSE_TOP1_HYPOTHESIS_AWAITING_THIRD_PERIOD_CONFIRMATION`
- status: WAITING

## 正式规则状态

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0
- formal model/data/config/CURRENT changes in state46: 0
- formal_weight for all research below: 0

## 当前研究分支

- branch: `research/draw-pit-availability-zero-label-20260814`
- latest persisted research HEAD: `2091fbd1e67bc581bf77525b076d3966a8adabef`
- branch purpose: R40 PIT availability zero-label feasibility / frozen prereg
- prior draw branch: `research/draw-subtype-mechanism-300-20260813`
- prior research HEAD: `b80cb24edc73ba7aa80d4a6c97e3207d92b32cac`
- Draft PR #191 remains Open / Draft / Unmerged / formal_weight=0

## 必须保留的既有正证据

### R24成熟历史概率确认

- fresh300 / history_n=30,395
- HDA LL delta -0.0088098
- Draw AUC delta +0.0293944
- bootstrap90 LL delta [-0.0173351,-0.0015384]
- probability gate PASS
- Top-1 Draw仍未解决

### R35-R38 Direct-T -> 条件D=0结构链

固定结构：直接预测 P(T=0,1,2,3,4,5,6,7+)，再预测条件 P(D=0|T,X)，最后自然汇总平局概率。

- R35 fresh300: core PASS；LL -0.0044166；AUC +0.0148452；bootstrap90 [-0.0085493,-0.0019548]
- R36 fresh300: LL -0.0041018；AUC +0.0028750；CI跨0
- R37 fresh300: LL -0.0015684；AUC +0.0020704；CI跨0
- R38 fresh1000: LL -0.0017259；AUC +0.0115026；CI跨0
- R35-R38 pooled 1900 diagnostic: LL -0.0025010；AUC +0.0092975；Brier -0.0016548；date-block bootstrap90 [-0.0044489,-0.0005596]
- pooled结果仅为诊断证据，不是预注册晋级门。

## 必须保留的既有反证

- R27B-R33 Top-1/选择性平局执行连续fresh验证未复制。
- R34 32-book盘口微观结构 fresh300 明确FAIL：LL +0.0078691；AUC -0.0351843；bootstrap90 [0.0004084,0.0168185]。
- R39 T-conditioned high-confidence selector fresh1000：固定 qD/market_pD Top30，仅7中；precision23.33%；recall2.78%；整体准确率-0.80pp；同窗概率层也轻微退化。R39 FAIL并封存。

## state46新增：R40/R41 PIT伤停与可用性研究

### R40零标签PIT可行性

- 数据源：公开FPL历史Git快照。
- 2020/21-2024/25 共恢复 1,738 场真实赛前PIT可用比赛，五季均 >=150。
- 零标签审计阶段目标比分/结果标签读取=0。
- zero-label workflow run: `31726018581`，success。
- Artifact: `9191280711`。
- manifest SHA256: `c1ad9abb3dc91af82f3e4c39bad530d9ffff8a5901ad8040c6c4bb0c2f936c26`。

### R40静态伤停存量残差

结论：明确FAIL。

- 619测试行。
- HDA LogLoss delta +0.030129。
- Draw AUC delta -0.050148。
- bootstrap90 LL delta [+0.016990,+0.043389]。
- 裁决：静态伤停数量/静态攻击缺口不能作为增量平局信号；不得继续在该家族调参。

### R41伤停/可用性“变化冲击”假设

R41与R40不同：不使用静态伤停存量，改为比较上一份真实PIT快照到本场PIT快照的状态变化，包括新伤、恶化、恢复及攻击角色可用性变化。

- 可恢复transition PIT约 1,686 场。
- 当前探索诊断使用2023/24+2024/25共609条已消费标签；因此不是独立OOS确认。
- 全局HDA LL delta +0.002107。
- Draw AUC delta -0.002534。
- bootstrap门未通过；R41不能作为全局概率层成功。
- 但自然argmax产生25次Top-1 Draw，命中11次：precision 44.0%，recall 8.27%。
- 2023/24：10次Top-1 Draw，5中；2024/25：15次Top-1 Draw，6中。
- 未使用forced Top-k，未做结果后阈值搜索。
- R41 prereg SHA256: `eea489b77ca4c89a9ab44681599c636f5dbc8d4df60040825798670b860fede1`。

## state46科学裁决

`STATIC_AVAILABILITY_FAILS_BUT_AVAILABILITY_CHANGE_MAY_BE_SPARSE_TOP1_GATE_NOT_CONFIRMED`

最准确表述：
- R35-R38证明Direct-T -> 条件D=0是目前更有依据的平局概率结构，但Top-1执行仍未解决。
- R40证明静态伤停存量没有增量价值。
- R41首次出现“赛前可用性短期变化可能作为稀疏Top-1触发信号”的新证据：25报11中/44%。
- 但R41使用的是已消费标签，且全局LL/AUC未改善，因此只能作为新假设，不能写成独立确认、可用模型或正式晋级。
- R41现有609行必须冻结，禁止调阈值、权重、特征或forced Top-k。

## 唯一下一步

若继续研究，只允许：

1. 寻找第三个未用于R41选择的时间段；
2. 先做零标签PIT覆盖门；
3. 保持同一冻结候选、同一特征和同一时点门；
4. 原样复核R41；
5. 若覆盖不足则放弃该时间段，不得放宽 `>=6h` 且 `<=10天陈旧` 的PIT门。

2025/26公开FPL历史只有约12次 `players_raw.csv` 提交，是否满足第三时间段覆盖门尚未完成审计；不得先看结果后放宽PIT条件。

## 完整R45B前向轨保持不变

- branch: `research/r45b-pit-role-availability-zero-label`
- HEAD: `c5625410463436c2d2bfa10b67634d92644dfdc6`
- fully eligible: 2 / 300
- remaining: 298
- independent_oos_authorized: false
- target labels / training / scoring / tuning / Provider / paid Provider / formal_weight: 全部0

## 其他边界

- 本state只发布接续状态，不继续研究、不读取新标签、不调参。
- 总进球正式轨仍暂停；R35-R38 Direct-T仅为平局挑战层。
- 不调用付费Provider。
- 正式模型、正式数据、正式config、CURRENT改动=0。

## 新对话恢复

START_HERE唯一版 -> ACTIVE_CHECKPOINT -> Airtable执行检查点 -> LAST_HANDOFF -> PROJECT_CURRENT -> Airtable当前状态/绑定维护日志 -> 唯一CURRENT。

恢复时必须看到 state46，并同时保留：R24/R35概率正证据、R34/R39失败反证、R40静态伤停FAIL、R41稀疏Top-1新假设未独立确认。

## Airtable锚点

- base: 足球项目接续
- current_state_record: `recs1pQ1rhuwJQAzE`
- maintenance_log: `recV6fQUxqADTqycH`
- execution_checkpoint: `recIRxK7EIMjJdG4A`
- airtable_state_version: 46
