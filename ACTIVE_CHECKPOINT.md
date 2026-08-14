# 足球项目实时执行检查点协议

本文件是稳定协议，不保存每次执行的动态子步骤。实时断点保存在 Airtable Base《足球项目接续》的唯一《执行检查点》激活记录中。

## 1. 目的

解决同一任务在以下情况下“刷新后又从头开始”或“高频执行已前进、正式接续层仍停在旧状态”的问题：

- ChatGPT 连接中断；
- 页面刷新或客户端重启；
- 长回复/多工具链被中断；
- 新建对话时上一轮存在未完成原子步骤；
- 研究连续推进，但 PROJECT_CURRENT / LAST_HANDOFF / Airtable《当前状态》未及时发布。

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

- `执行检查点`：高频、细粒度；允许在同一 state_version 下多次更新 checkpoint_version。
- `LAST_HANDOFF.md`：每次实质进展/方向变化/回答收尾更新，记录跨对话末端。
- `PROJECT_CURRENT.md`：项目任务、研究门、HEAD、权限、科学结论等重要状态变化时递增 state_version。
- Airtable《维护日志》：重要状态变化追加，不为每个检查点版本追加日志。

## 6. state_version 双边发布闭环

凡是会产生新的 `state_version` 的实质状态变化，必须视为一次不可拆散的“状态发布事务”。新版本只有在 GitHub 与 Airtable 双边都真实落盘并完成回读一致性核验后，才算已发布。

### 新 state_version 的完成条件

以下条件必须同时成立：

1. Airtable《维护日志》中存在建立该 `state_version` 的绑定日志，且其 record ID 已写入《当前状态》的“状态日志记录ID”；
2. GitHub main 的 `PROJECT_CURRENT.md` 明确写入同一 `state_version`；
3. GitHub main 的 `LAST_HANDOFF.md` 明确写入同一 `state_version`；
4. Airtable《当前状态》的“状态版本”与 GitHub 两个接续文件一致；
5. Airtable《当前状态》的“main SHA”必须等于 GitHub `main` 分支真实 HEAD；研究分支 HEAD 必须单独写入“当前HEAD”；
6. Airtable保存的 `PROJECT_CURRENT.md SHA-256` 与 `LAST_HANDOFF.md SHA-256` 必须与 GitHub main 上对应文件完整 UTF-8 内容一致；
7. Airtable《当前状态》的“执行检查点版本”必须等于唯一激活《执行检查点》的 `checkpoint_version`；
8. Airtable保存的 `ACTIVE_CHECKPOINT.md SHA-256` 必须与 GitHub main 上本文件完整 UTF-8 内容一致；
9. 完成后必须回读 GitHub main、三份接续文件、Airtable《当前状态》和唯一激活《执行检查点》，确认全部一致。

### 发布纪律

- 新 `state_version` 发布开始前，执行检查点必须先进入 `RUNNING`。
- 双边闭环完成前，不得把该步骤标记为 `COMPLETED / WAITING`，也不得在新对话中把该版本当作已发布状态继续研究。
- 推荐顺序：检查点 `RUNNING` → 追加建立新state的维护日志 → 更新 `ACTIVE_CHECKPOINT.md`（若协议变化）→ 更新 `PROJECT_CURRENT.md` → 更新 `LAST_HANDOFF.md` → 更新 Airtable《当前状态》的版本、真实 main SHA、研究 HEAD、绑定日志ID、三个文件SHA与checkpoint_version → 全量回读核验 → 检查点 `COMPLETED / WAITING`。
- 若中途任何一边写入失败，检查点必须保持 `RUNNING / VERIFYING`；无法在当前执行中补齐时改为 `BLOCKED`，原因固定为 `BLOCKED_STATE_PUBLICATION_INCOMPLETE`。
- 若发布过程中发现另一个对话已经生成更高 `state_version`，立即停止当前写入，禁止用较低版本覆盖较高版本；先重新读取更高版本并重新做一致性核验。

## 7. 实质科学结论前置发布硬门（2026-08-14新增）

这是防止“检查点已经推进、正式接续层仍停在旧state”再次发生的强制门。

### 触发条件

出现以下任一情况，视为“实质科学状态变化”，必须先发布新的 state_version：

- 一个预注册/滚动/OOS/开发实验得出新的 PASS / FAIL / STOP / 封存裁决；
- 研究瓶颈、唯一下一方向或禁止继续的模型家族发生改变；
- 新的 research HEAD / branch / PR / run / Artifact 成为后续恢复必需锚点；
- 标签门、PIT覆盖门、独立OOS授权状态发生变化；
- 用户明确改变研究主线或冻结/解冻一个确认窗口。

### 硬门规则

1. 一旦触发，当前原子步骤完成后，检查点必须进入 `STATE_PUBLICATION_REQUIRED` 语义状态；如枚举中无该值，则保持 `RUNNING/VERIFYING` 并在“当前子步骤”写明 `STATE_PUBLICATION_REQUIRED`。
2. **在新 state_version 双边发布闭环完成前，禁止启动下一项实质研究原子步骤。**
3. 禁止出现“连续两个及以上实质科学结论只写维护日志/检查点、但 PROJECT_CURRENT/LAST_HANDOFF 不发布”的情况。
4. 启动任何新研究前必须比较：`PROJECT_CURRENT.state_version == Airtable当前状态.state_version == 执行检查点.state_version` 且 `当前状态.执行检查点版本 == 激活检查点.checkpoint_version`。任一不等，停止为 `BLOCKED_STATE_PUBLICATION_LAG`。
5. 若维护日志存在高于 PROJECT_CURRENT 的 state_version，或存在更新的实质科学结论但没有绑定 state_version，必须先做状态修复，禁止继续研究。

## 8. 失败关闭

出现以下情况必须停止：

- 多条《执行检查点》记录同时 `是否激活=true`；
- 检查点项目ID不是 `football-project`；
- 检查点状态版本高于 PROJECT_CURRENT，且不是正在执行已登记的state publication事务；
- RUNNING 步骤的副作用无法判断是否发生；
- 检查点要求重复执行 LAST_HANDOFF/PROJECT_CURRENT 已明确完成且禁止重复的事项；
- 检查点扩大用户授权范围；
- 新 `state_version` 只写入 GitHub 或只写入 Airtable，未完成第6节双边发布闭环；
- 触发第7节实质科学结论门后仍试图启动下一研究步骤。

## 9. 当前长期硬约束

执行检查点不能绕过唯一 CURRENT、研究预注册、标签门、训练授权、Provider预算、PR合并授权或任何正式硬门。

普通“继续”只能恢复既有授权范围；不得因为存在检查点而自动获得新的独立 OOS、标签读取、训练、评分、正式晋级或付费接口授权。
