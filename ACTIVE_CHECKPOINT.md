# 足球项目实时执行检查点协议

本文件是稳定协议，不保存动态子步骤。实时断点只保存在 Airtable Base《足球项目接续》的唯一《执行检查点》激活记录中。

## 1. 目的

解决刷新、断线、新对话、长任务换挡后重复执行或恢复到旧位置的问题，同时避免启动阶段为了接续而读取大量历史。

执行检查点只负责“这一轮执行到哪里”；不替代唯一正式 CURRENT、`PROJECT_CURRENT.md`、`LAST_HANDOFF.md` 或 Airtable《当前状态》。

## 2. 快速恢复：FAST_CHECKPOINT

新对话/刷新默认不先读取本协议全文。启动器应按 `AGENTS.md` 的 FAST_BOOTSTRAP 执行：

1. 读 Airtable《当前状态》唯一激活记录；
2. 按其中“执行检查点记录ID”精确读取那一条检查点；
3. 只核验 GitHub `main` 当前 HEAD SHA。

若 `project_id / state_version / checkpoint_version / main SHA` 四项一致，即可按检查点恢复，不要求每次新对话再全文读取 `ACTIVE_CHECKPOINT.md / LAST_HANDOFF.md / PROJECT_CURRENT.md / 维护日志`。

只有发生 main SHA漂移、版本冲突、RUNNING/VERIFYING副作用核验、状态发布/修复或当前任务确实需要时，才扩大到相关 GitHub 文件和绑定维护日志。

### 检查点状态

- `WAITING / COMPLETED / IDLE`：从“恢复位置/唯一下一步”继续，不重做“已完成事项”；
- `RUNNING / VERIFYING`：禁止直接重试，先核验外部副作用；
- `BLOCKED`：保持阻塞，直到获得新证据或用户改变范围；
- 检查点 state_version 与当前状态不一致：停止并进入扩展核验。

## 3. 原子步骤写入纪律

只对可恢复原子步骤打点，不对每个只读调用打点。

典型原子步骤：一次PR/文件写入、一次workflow dispatch+验收、一批数据处理、一次Airtable/仓库治理写入、一次可能产生外部副作用的操作。

### 开始前

检查点设为 `RUNNING`，checkpoint_version +1，至少写入：

- 父任务；
- 操作ID（幂等键）；
- 当前子步骤；
- 上一已完成子步骤；
- 恢复位置；
- 目标对象；
- 预期副作用；
- 副作用状态=`PENDING/NONE`；
- 可安全重试；
- 用户授权范围；
- 禁止重复；
- 唯一下一步。

### 完成后

先核验真实结果，再写 `COMPLETED / WAITING / BLOCKED`，记录真实证据（HEAD/PR/run/job/record ID/SHA等）、已完成事项、恢复位置和唯一下一步。

不得只在聊天里说“完成”而不更新检查点。

## 4. 幂等恢复

检查点为 `RUNNING / VERIFYING` 时：

1. 按 `操作ID + 目标对象 + 预期副作用` 查询真实外部状态；
2. 已发生：禁止重复执行，补写完成状态；
3. 明确未发生且 `可安全重试=true`：只允许重试同一操作ID；
4. 无法判断：`副作用状态=AMBIGUOUS`、检查点=`BLOCKED`，停止为 `BLOCKED_CHECKPOINT_SIDE_EFFECT_AMBIGUOUS`；
5. 禁止为了“保险”重复创建PR、重复写记录或重复付费/副作用调用。

## 5. 与长期状态的分工

- 《执行检查点》：实时高频断点，同一 state_version 下可多次增加 checkpoint_version；
- `LAST_HANDOFF.md`：重要对话末端与接力说明；
- `PROJECT_CURRENT.md`：重要项目/研究状态与 state_version；
- Airtable《当前状态》：结构化快速启动入口；
- Airtable《维护日志》：追加式历史证据。

FAST_BOOTSTRAP 通过时，新对话不需要读取全部长期层。长期层只在冲突、状态发布、状态修复或任务证据需要时展开。

## 6. state_version 双边发布闭环

凡产生新 state_version，视为不可拆散的状态发布事务。只有 GitHub 与 Airtable 双边真实落盘并回读一致才算发布完成。

必须同时满足：

1. Airtable《维护日志》存在建立该 state_version 的绑定记录；
2. `PROJECT_CURRENT.md` 与 `LAST_HANDOFF.md` 写入同一 state_version；
3. Airtable《当前状态》state_version 一致；
4. Airtable `main SHA` 等于 GitHub main 真实 HEAD；
5. 需要记录的 `PROJECT_CURRENT / LAST_HANDOFF / ACTIVE_CHECKPOINT` SHA 与 GitHub内容一致；
6. 当前状态 checkpoint_version 等于唯一激活检查点；
7. 全量回读核验通过。

推荐顺序：检查点 RUNNING → 追加绑定维护日志 → 更新必要协议/PROJECT_CURRENT/LAST_HANDOFF → 更新当前状态的版本、main SHA、研究HEAD、绑定日志ID、文件SHA与checkpoint_version → 回读核验 → 检查点 WAITING/COMPLETED。

中途失败保持 `RUNNING/VERIFYING`；无法补齐则 `BLOCKED_STATE_PUBLICATION_INCOMPLETE`。发现更高 state_version 时立即停止低版本覆盖。

## 7. 实质科学结论硬门

以下任一情况必须先发布新 state_version，再开始下一实质研究步骤：

- 新 PASS / FAIL / STOP / 封存裁决；
- 研究瓶颈、唯一下一方向或禁止模型家族改变；
- 新 research HEAD / branch / PR / run / Artifact 成为恢复锚点；
- 标签门、PIT覆盖门、独立OOS授权状态改变；
- 用户改变研究主线或冻结/解冻确认窗口。

发布前若必须等待，检查点保持 `RUNNING/VERIFYING` 并明确 `STATE_PUBLICATION_REQUIRED`。禁止连续产生实质科学结论却只写维护日志/检查点、不更新正式接续层。

## 8. 失败关闭

出现以下情况必须停止：

- 多条检查点同时激活；
- project_id 不是 `football-project`；
- 当前状态与检查点 state_version/checkpoint_version 冲突；
- RUNNING步骤副作用无法判断；
- 检查点要求重复已明确完成且禁止重复的事项；
- 检查点扩大用户授权；
- 新 state_version 只写GitHub或只写Airtable；
- 触发科学结论门后仍试图开始下一研究步骤。

## 9. 权限边界

检查点不能绕过唯一 CURRENT、预注册、标签门、训练授权、Provider预算、PR Ready/合并授权或任何正式硬门。

普通“继续”只恢复既有授权；不得自动获得新的独立OOS、标签读取、训练、评分、正式晋级或付费接口权限。

## 10. EXECUTION_LITE 执行链硬门

FAST_BOOTSTRAP 通过后，所有跨系统连续执行还必须遵守仓库 `EXECUTION_LITE.md`。

默认执行块：
- FAST_BOOTSTRAP 的3个小读取不计预算；
- 每个后续执行块最多6次外部工具调用；
- 每块最多1次重型读取、1个主要副作用动作；
- 第5次调用时若仍需新的重型读取，先写 WAITING/适当终态再进入下一块；
- 同一用户回合默认最多连续2个执行块，达到后先形成安全断点。

窄任务禁止递归整仓库树、整表扫描、整Actions历史、重复connector schema discovery或反复展开相同大日志/Artifact。已知 record ID、path、HEAD、PR/run/job/Artifact ID 时必须精确读取。

ZIP/raw bytes、大批数据、大日志、大Artifact与批量模型计算优先下放 GitHub Actions/容器，聊天只负责冻结输入、精确核验结果和写 checkpoint。

出现输出截断、连续connector错误/超时、需要下一次重型查询或已完成两个执行块时，必须尽快收口为 WAITING/适当终态，禁止继续无限叠加工具调用。

本协议仅优化恢复路径与执行负载，不改变模型、数据、CURRENT、科学设计或正式权重。