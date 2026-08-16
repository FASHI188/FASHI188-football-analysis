# 足球项目最后接力点

> 这是重要接力说明；实时断点以 Airtable《当前状态》+《执行检查点》为准。新对话或新项目首先执行 FAST_BOOTSTRAP，不扫描旧聊天。

## 接力身份

- project_id: `football-project`
- handoff_version: **28**
- state_version: **57**
- checkpoint_version: **82**
- updated_at_utc: `2026-08-16T09:02:36Z`
- status: `WAITING`
- state_log_record_id: `recRUK2uiHlNQBllf`

## 当前真实断点

研究主线仍是：**寻找真正能提升总进球 T 的赛前信息。**

最新持久化推进：

- multi-OU 公开源技术可行性已经记录为 PASS；
- 这只证明单赛事/少量赛事可以拿到多档 OU 市场信息；
- **批量未来赛事 coverage / synchronization gate 尚未完成**；
- 因此不能启动正式 forward 标签积累，也不能把市场源路线宣布为已可用。

## 唯一下一步

批量抽取未来未开赛赛事，验证同一短时间窗口内能否稳定获得：

- Match Odds；
- OU1.5 / 2.5 / 3.5 / 4.5；
- back / lay；
- matched amount；
- observed_at；
- kickoff；
- source / hash。

裁决：

- PASS → 启动 forward 多线 OU 冻结与后续 settlement 验证；
- FAIL → 停止市场源路线，转 observed_at XI / injury / availability。

## 禁止重复/越界

- 不重复已有 multi-OU 技术可行性探测；
- 不把批量 coverage gate 写成已经 PASS；
- 不恢复独立 Parity 硬分类作为主任务；
- 不用 Direct-T logistic / 层级 / 桶壳搜索代替信息源研究；
- 不读赛果完成未来赛事覆盖门；
- 不新增付费 Provider；
- 不修改 formal model/data/config/CURRENT；
- 不因旧聊天、旧项目或记忆出现更详细文字就回退到更低 state/checkpoint。

## 为什么从 state56 升到 state57

Airtable 已经推进到 checkpoint81 和新的 multi-OU 技术 PASS，但 GitHub `PROJECT_CURRENT.md` / `LAST_HANDOFF.md` 仍停在 checkpoint76，state_version 仍为56。按项目硬门，新的科学 PASS 或唯一下一步变化必须发布新 state_version。

state57 只修复这次接续漂移并固化跨项目恢复；formal资产改动为0。

## 跨项目恢复

以后即使“足球2”满了并新建“足球3/4”，**不要复制旧项目聊天历史**。新项目直接使用同一外部身份：

- GitHub: `FASHI188/FASHI188-football-analysis`
- Airtable: `足球项目接续` (`appLXF9IBvSCEUjJV`)

默认只做3个小读取：

1. Airtable《当前状态》唯一激活记录；
2. 按其中“执行检查点记录ID”精确读唯一检查点；
3. 核验 GitHub main HEAD。

`project_id / state_version / checkpoint_version / main SHA` 一致即恢复成功。旧项目记忆不是接续前提。

## 关键锚点

- current_state: `recs1pQ1rhuwJQAzE`
- execution_checkpoint: `recIRxK7EIMjJdG4A`
- state57 creation log: `recRUK2uiHlNQBllf`
- state_version: **57**
- checkpoint_version: **82**

## 新对话/新项目收到“继续”时

只执行 FAST_BOOTSTRAP。通过后直接从批量 multi-OU coverage / synchronization gate 继续；不得要求用户重新讲历史，也不得先回放旧研究。
