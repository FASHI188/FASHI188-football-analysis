# 足球项目实时执行检查点协议

本文件是稳定协议，不保存每次执行的动态子步骤。实时断点保存在 Airtable Base《足球项目接续》的唯一《执行检查点》激活记录中。

## 1. 目的

解决同一任务在以下情况下“刷新后又从头开始”的问题：

- ChatGPT 连接中断；
- 页面刷新或客户端重启；
- 长回复/多工具链被中断；
- 新建对话时上一轮存在未完成原子步骤。

`执行检查点`只负责“这一轮执行到哪一步”，不替代：

- `LAST_HANDOFF.md`：上一条对话末端；
- `PROJECT_CURRENT.md`：项目/研究状态；
- 唯一 CURRENT：正式规则。

## 2. 启动与快速恢复顺序

进入足球项目后，先读取本协议，再读取 Airtable《执行检查点》唯一激活记录，然后读取 `LAST_HANDOFF.md`、`PROJECT_CURRENT.md`、Airtable《当前状态》和绑定维护日志。

只有完成一致性核验后才允许执行。

### 快速恢复判定

- 检查点 `状态版本 == PROJECT_CURRENT.state_version`：检查点可参与恢复。
- 检查点状态为 `WAITING / COMPLETED / IDLE`：从“恢复位置/唯一下一步”继续，不重做“已完成事项”。
- 检查点状态为 `RUNNING / VERIFYING`：不得直接重试；必须先核验“预期副作用”是否已经发生。
- 检查点状态为 `BLOCKED`：保持阻塞，除非获得新证据或用户明确改变范围。
- 检查点状态版本小于 PROJECT_CURRENT：视为旧检查点，不恢复；回退到 LAST_HANDOFF。
- 检查点状态版本大于 PROJECT_CURRENT：停止为 `BLOCKED_CHECKPOINT_STATE_MISMATCH`。

## 3. 原子步骤写入纪律

只对“可恢复原子步骤”打点，不对每个只读工具调用打点，避免噪声。

典型原子步骤包括：

- 一场 fixture bundle 的完整前向采集；
- 一次 PR/文件写入；
- 一次 Actions workflow 启动与结果核验；
- 一批公开源采集；
- 一次 Airtable/仓库治理写入；
- 一次可能产生外部副作用的操作。

### 步骤开始前

把唯一激活检查点更新为 `RUNNING`，至少写入：

- checkpoint_version +1；
- 父任务；
- 唯一 `操作ID`（幂等键）；
- 当前子步骤；
- 上一已完成子步骤；
- 恢复位置；
- 目标对象；
- 预期副作用；
- 副作用状态=`PENDING` 或 `NONE`；
- 可安全重试；
- 用户授权范围；
- 禁止重复事项。

### 步骤完成后

先核验真实结果，再把检查点更新为 `COMPLETED` 或 `WAITING`：

- 副作用状态=`VERIFIED_PRESENT / VERIFIED_ABSENT / NONE`；
- 写入真实证据（HEAD/PR/run/job/record ID/文件 SHA 等）；
- 更新已完成事项；
- 明确新的恢复位置与唯一下一步。

不得只在聊天里说“完成”而不更新检查点。

## 4. 中断后的幂等恢复

如果刷新后看到 `RUNNING / VERIFYING`：

1. 先按 `操作ID + 目标对象 + 预期副作用` 查询真实外部状态；
2. 若副作用已发生：禁止重复执行，直接补写为 `COMPLETED` 并继续下一步；
3. 若明确未发生且 `可安全重试=true`：允许重试同一 `操作ID`；
4. 若无法判断是否已发生：设置 `副作用状态=AMBIGUOUS`、检查点状态=`BLOCKED`，停止为 `BLOCKED_CHECKPOINT_SIDE_EFFECT_AMBIGUOUS`；
5. 禁止为了“保险”重复创建 PR、重复写记录、重复调用付费/有副作用接口。

## 5. 与 LAST_HANDOFF / PROJECT_CURRENT 的分工

- `执行检查点`：高频、细粒度；允许在同一 state_version 下多次更新 checkpoint_version，不触发 PROJECT_CURRENT state_version 递增。
- `LAST_HANDOFF.md`：每次实质进展/方向变化/回答收尾更新，记录跨对话末端。
- `PROJECT_CURRENT.md`：只有项目任务、研究门、HEAD、权限、科学结论等重要状态变化时才递增 state_version。
- Airtable《维护日志》：重要状态变化追加，不为每个检查点版本追加日志。

这样可以避免为了保存第几个子步骤而产生大量 main 提交和 Actions。

## 6. 失败关闭

出现以下情况必须停止：

- 多条《执行检查点》记录同时 `是否激活=true`；
- 检查点项目ID不是 `football-project`；
- 检查点状态版本高于 PROJECT_CURRENT；
- RUNNING 步骤的副作用无法判断是否发生；
- 检查点要求重复执行 LAST_HANDOFF/PROJECT_CURRENT 已明确完成且禁止重复的事项；
- 检查点扩大用户授权范围。

## 7. 当前长期硬约束

执行检查点不能绕过唯一 CURRENT、研究预注册、标签门、训练授权、Provider预算、PR合并授权或任何正式硬门。

普通“继续”只能恢复既有授权范围；不得因为存在检查点而自动获得新的独立 OOS、标签读取、训练、评分、正式晋级或付费接口授权。
