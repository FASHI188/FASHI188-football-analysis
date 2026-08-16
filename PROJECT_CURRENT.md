# 足球项目当前状态

## 身份

- project_id: `football-project`
- state_version: **57**
- updated_at_utc: `2026-08-16T09:02:36Z`
- updated_by: `GPT-5.6 Sol`
- status_source: `STATE57_HANDOFF_DRIFT_REPAIR_MULTI_OU_COVERAGE_GATE`
- status: `WAITING`
- state_log_record_id: `recRUK2uiHlNQBllf`
- predecessor_state: 56

## 正式规则与正式资产

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- formal model/data/config/CURRENT changes in state57: **0**
- formal_weight changes in state57: **0**

state57 只修复接续状态漂移并正式发布已经存在的最新研究断点，不修改正式模型、正式数据、正式配置或 CURRENT，不启动新实验。

## 为什么发布 state57

Airtable《当前状态》和《执行检查点》已经推进到 checkpoint81：`multi-OU公开源技术可行性PASS`，唯一下一步变为批量未来赛事 `coverage/synchronization gate`；但长期层仍停在 state56 / checkpoint76。

这违反 `AGENTS.md` / `ACTIVE_CHECKPOINT.md` 的状态发布硬门：新的科学 PASS 或唯一下一步变化必须先发布新 `state_version`。state57 用于把 GitHub 与 Airtable 恢复到同一真实断点，并增加跨项目恢复约束。

## 当前研究状态

### 上游诊断仍有效

500场 T / Parity / GD 诊断的核心方向保持不变：

1. 知道真实 Parity 时性能明显恢复；
2. 知道完整真实 T 时进一步恢复；
3. 现有 X 连 Parity 都预测不好；
4. 第一优先仍是寻找真正能提升 T 的赛前信息；
5. 第二优先仍是偶数 T 下解决 `GD=0` vs `GD=±2`；
6. 不把独立 Parity 硬分类或 Direct-T 外壳搜索恢复为主路线。

上述500场精确指标如未绑定原 result/commit/run/Artifact，仍不得伪装为 VERIFIED。

### 最新推进：multi-OU 技术可行性

当前持久化状态已记录：**multi-OU 公开源技术可行性 PASS**。

检查点81记录的技术证据包括：

- Betfair Exchange 单赛事页可同时暴露多档总进球市场；
- 实测页面可解析 OU1.5 / 2.5 / 3.5 的 back / lay 与 matched amount；
- 页面菜单存在更宽 OU 档位；
- 不同赛事页面均出现多档 OU，说明不是单页偶发现象。

本次 state57 只验证“该结论已经存在于当前权威接续状态并应被发布”，**没有重新抓取网页重做技术实验**。

### 尚未通过的门

**批量未来赛事 coverage / synchronization gate 尚未完成，禁止写成 PASS。**

需要在一批未来未开赛赛事上验证能否在同一短窗口获得：

- Match Odds；
- OU1.5 / 2.5 / 3.5 / 4.5（至少这些核心档位）；
- back / lay；
- matched amount；
- observed_at；
- kickoff；
- source / hash。

## 唯一下一步

**批量抽取未来未开赛赛事，完成 multi-OU coverage / synchronization gate。**

裁决：

- PASS → 才允许启动 forward 多线 OU 冻结与后续 settlement 验证；
- FAIL → 停止市场源路线，转向 observed_at XI / injury / availability；
- 未完成批量门之前，不积累正式 forward 标签，不宣布市场源可用。

## 当前禁止事项

- 禁止独立 Parity 硬分类作为主路线；
- 禁止继续围绕 Direct-T logistic / 层级 / 桶壳搜索；
- 禁止把 multi-OU 技术可行性 PASS 等同于批量 coverage PASS；
- 禁止读取赛果来完成未来赛事 coverage 门；
- 禁止付费 Provider；
- 禁止修改 formal model/data/config/CURRENT；
- 禁止因旧聊天或旧项目记忆而回退到更低 state_version/checkpoint_version。

## 跨项目永久接续规则

ChatGPT 项目只是工作窗口，不是唯一状态存储。

无论当前工作窗口叫“足球2”“足球3”还是以后新的足球项目，恢复都使用同一外部身份：

- project_id: `football-project`
- GitHub: `FASHI188/FASHI188-football-analysis`
- Airtable Base: `足球项目接续` (`appLXF9IBvSCEUjJV`)

新项目**不需要搬旧聊天、不需要读取旧项目历史**。默认 FAST_BOOTSTRAP 只做：

1. Airtable《当前状态》唯一激活记录；
2. 按其中记录ID精确读取唯一《执行检查点》；
3. 核验 GitHub `main` 当前 HEAD。

四项 `project_id / state_version / checkpoint_version / main SHA` 一致即可恢复。旧项目聊天只能按需作历史证据，不能决定恢复点。

## Airtable锚点

- base: `足球项目接续` (`appLXF9IBvSCEUjJV`)
- current_state_record: `recs1pQ1rhuwJQAzE`
- state57 creation log: `recRUK2uiHlNQBllf`
- execution_checkpoint_record: `recIRxK7EIMjJdG4A`
- checkpoint_version: **82**

## 权威优先级

当前用户明确指令 > 唯一正式 CURRENT > 最新 Airtable state/checkpoint > GitHub 当前状态文件 > 历史聊天/旧文件/记忆。

对“从哪里继续”，更高 `state_version` 永远覆盖更低版本；同一 state 下，更高 `checkpoint_version` 永远覆盖更低版本。
