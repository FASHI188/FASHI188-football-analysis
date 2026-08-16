# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 56
- updated_at_utc: 2026-08-16T08:12:00Z
- updated_by: GPT-5.6 Sol
- status_source: `STATE56_HANDOFF_REPAIR_POST500_T_INFORMATION_PRIORITY`
- status: WAITING
- state_log_record_id: `recxp2m0KCfSbChhW`
- predecessor_state: 55

## 正式规则与正式资产

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- formal model/data/config/CURRENT changes in state56: 0
- formal_weight changes in state56: 0

本次 state56 只修复接续状态，不修改正式模型、正式数据、正式配置或 CURRENT，也不启动新实验。

## 为什么发布 state56

上一对话的实际研究已经越过 state55/checkpoint75：完成了一轮 500 场 T / Parity / GD 诊断，并据此改变了下一研究主方向；但该结论没有及时写入 Airtable/GitHub 接续层。新对话因此按旧 FAST_BOOTSTRAP 错误恢复到“仓库减负治理”。

state56 的目的就是把**真实最新研究断点**写回持久化层，并增加强制落盘门，防止再次因对话满/刷新而倒退。

## 最新500场诊断断点

证据分类：`REPORTED_NOT_VERIFIED`。

原因：当前用户已在本对话明确重述上一对话末端结论，但当前仓库代码搜索未定位到该最新500场诊断对应的精确 result file / commit / run / job / Artifact。以下精确结果不得伪装为 VERIFIED；以后若找到原结果锚点，再追加证据，不改变当前恢复方向。

用户确认的上一对话末端结论：

1. **知道真实 Parity（总进球奇偶）时，性能大幅恢复。**
2. **知道完整真实 T（总进球数）时，性能进一步大幅恢复。**
3. **现有 X 连 Parity 都预测不出来。**
4. 因此主要瓶颈已从“Direct-T 算法外壳/模型结构”转向：**缺少真正解释比赛总进球状态的赛前信息。**
5. 即使知道真实 T，GD 仍未解决：
   - 模型预测平局 201 场；
   - 其中 81 场是假平局；
   - 2:2 仅识别 17/32；
   - 高比分平局更差。
6. 500 场诊断的核心价值：把“模型结构有毛病”与“输入信息不够”分开。

## 当前研究优先级

优先级固定为：

### 第一优先
**寻找真正能够提升 T 的赛前信息源。**

研究问题变为：
> 什么在开赛前可获得、严格 PIT 合法的信息，能够解释比赛总进球状态 T？

不再把主要时间花在同一批 X 上继续更换：
- logistic；
- Direct-T 层级；
- 分类桶；
- 其他只改变算法外壳但不增加信息量的变体。

### 第二优先
**偶数 T 条件下继续解决 `GD=0` vs `GD=±2` 的分配。**

即使 T 为 oracle，平局/非平局分配仍有明显剩余误差，尤其高比分平局。

### 明确停止的主路线
**不再做独立 Parity 硬分类作为主研究路线。**

Parity 只保留为诊断证据：它证明 T 状态信息有价值，但最终目标是获得更完整的 T 信息，而不是先造一个奇偶分类器再绕回来。

## 唯一下一步

从现有历史数据和已有赛前可得字段开始，系统盘点并测试**能够提升 T 的真实赛前信息源**：

1. 优先现有数据，不新增付费 Provider；
2. 逐类盘点当前未进入 T 研究的赛前字段/信息；
3. 严格 PIT，禁止赛后泄漏；
4. 在固定历史样本上做小步增量测试；
5. 先判断信息是否增加对 T 的可预测性，再讨论模型形式；
6. 第一优先未有结果前，不切回独立 Parity 硬分类或 Direct-T 外壳搜索。

## 接续强制落盘门

从 state56 起执行以下硬门：

1. 任何研究结果只要改变**当前瓶颈、主方向、优先级、停止条件或唯一下一步**，就属于重要状态变化；
2. 这种变化必须在同一工作回合内先写入 Airtable《执行检查点》；若构成主方向变化，同时发布新 `state_version`；
3. 不允许等到对话快满才补接力；
4. 在状态尚未持久化前，不得对用户声称“新对话可以直接接上”；
5. 若持久化失败，必须明确输出 `HANDOFF_NOT_PERSISTED`，并保留当前对话，不得假装已经安全接续；
6. 长任务每个实质执行块结束后都更新 checkpoint 的“上一已完成 / 恢复位置 / 唯一下一步 / 禁止重复”；
7. 新对话 FAST_BOOTSTRAP 只以最新持久化 state/checkpoint 为恢复点，旧 state 不得覆盖更高 state_version。

## 当前禁止事项

- 不把 state55 的“仓库治理”恢复成当前研究主线；
- 不重复 R55B；
- 不把上述500场精确指标标成 VERIFIED，除非找到原 result/commit/run/Artifact 锚点；
- 不新增付费 Provider；
- 不修改正式模型、正式数据、正式 config 或 CURRENT；
- 不独立启动 Parity 硬分类作为主路线；
- 不继续把换 logistic / 层级 / 分类桶当作 T 问题的首要解法。

## Airtable锚点

- base: `足球项目接续`
- current_state_record: `recs1pQ1rhuwJQAzE`
- state56 creation log: `recxp2m0KCfSbChhW`
- execution_checkpoint_record: `recIRxK7EIMjJdG4A`
- checkpoint_version: 76

## 权威优先级

当前用户明确指令 > 唯一正式 CURRENT > 最新 Airtable state/checkpoint > LAST_HANDOFF > PROJECT_CURRENT + 绑定维护日志 > 历史聊天/旧文件/记忆。

对“从哪里继续”这一问题，**更高 state_version 永远覆盖更低 state_version**；不得因旧 checkpoint 内容更详细而回退。
