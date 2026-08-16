# 足球项目最后对话接力点

> 新对话首先执行 FAST_BOOTSTRAP；当前动态恢复点以 Airtable《当前状态》+《执行检查点》为准。正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 27
- state_version: 56
- checkpoint_version: 76
- updated_at_utc: 2026-08-16T08:12:00Z
- status: WAITING
- state_log_record_id: `recxp2m0KCfSbChhW`

## 为什么这是新的真实断点

state55/checkpoint75 已过时。

上一对话在 state55 之后继续做了 500 场 T / Parity / GD 诊断，并形成了新的研究方向；该结果当时没有写回持久化接续层，导致本次新对话错误恢复到旧的“仓库减负治理”。

用户已在当前对话重新确认上一对话末端结论，因此 state56 把它正式写成新的恢复点。

## 最新500场研究结论

证据分类：`REPORTED_NOT_VERIFIED`。

当前未在仓库代码搜索中定位到这批最新500场实验的精确 result/commit/run/job/Artifact；不得伪造 VERIFIED 锚点。恢复方向由当前用户明确确认，因此作为当前权威工作断点。

结论：

- 知道真实 Parity → 性能大幅恢复；
- 知道完整真实 T → 性能进一步大幅恢复；
- 现有 X → 连 Parity 都预测不出来；
- 主要瓶颈因此定位为**赛前输入信息不足**，而不是继续更换 Direct-T 算法外壳；
- 即使知道真实 T，GD 仍未解决：
  - 预测平局 201 场；
  - 81 场是假平局；
  - 2:2 只识别 17/32；
  - 高比分平局更差。

## 当前优先级

1. **第一优先：找能提升 T 的真实赛前信息源。**
2. **第二优先：偶数 T 下解决 `GD=0` vs `GD=±2`。**
3. **不再做独立 Parity 硬分类作为主路线。**
4. **不再围绕 Direct-T 换 logistic / 换层级 / 换分类桶作为首要方向。**

## 唯一下一步

直接从“寻找真正解释总进球 T 的赛前信息”继续：

- 优先盘点现有历史库、已有赛前字段和此前未用于 T 研究的功能性信息；
- 严格 PIT；
- 固定历史样本做增量测试；
- 先验证信息增益，再决定模型形式；
- 不新增付费 Provider，除非用户另行明确授权。

## 禁止重复/越界

- 不恢复 state55 的仓库治理作为当前主线；
- 不重复 R55B；
- 不重新把独立 Parity 分类当主任务；
- 不用同一 X 反复换算法外壳代替信息源研究；
- 不把未绑定 Artifact 的500场精确指标写成 VERIFIED；
- 不修改 formal model/data/config/CURRENT；
- 不调用付费 Provider。

## 强制接续规则

以后任何会改变“瓶颈/方向/优先级/唯一下一步”的研究结论，必须**当场落 Airtable checkpoint**；构成重要方向变化时同时发布新 state_version。

若还没落盘，不得告诉用户“可以新建对话直接接上”。若写入失败，必须明确标记 `HANDOFF_NOT_PERSISTED`。

长任务每个实质执行块结束后都要写“上一已完成 / 恢复位置 / 唯一下一步 / 禁止重复”，不要等对话快满。

## 关键锚点

- state_version: 56
- checkpoint_version: 76
- state56 creation log: `recxp2m0KCfSbChhW`
- current_state: `recs1pQ1rhuwJQAzE`
- execution_checkpoint: `recIRxK7EIMjJdG4A`

## 新对话恢复

新对话收到“继续 / 开始 / 接上上一个对话”时：

1. 读取 current_state；
2. 精确读取 checkpoint76；
3. 核验 GitHub main SHA；
4. FAST_BOOTSTRAP 通过后，**直接从 T 信息源研究继续**。

不得回退到 state55/checkpoint75。
