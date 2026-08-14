# 足球项目新对话与中断恢复规则

本文件是足球项目长期固定的接续治理规则，不记录动态 PR、HEAD、研究结果或模型结论。

动态接续分为四层：

- `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》：同一任务当前执行到哪个原子子步骤，负责“刷新/断线后从哪继续”。
- `LAST_HANDOFF.md`：上一条对话末端如何继续，负责“接上上一条”。
- `PROJECT_CURRENT.md`：当前项目/工作对象状态，负责“接得对不对”。
- Airtable Base《足球项目接续》：结构化当前状态、执行检查点与维护日志，负责交叉验证和长期留痕。

任何旧聊天、旧 README、旧待办、旧接续总表都不得替代这四层。

## 1. 新对话/刷新启动顺序

每次进入足球项目，无论首条消息是“继续”“接上上一条”“继续足球项目”“足球研究”还是具体任务，实际执行前依次读取：

1. 仓库根目录 `ACTIVE_CHECKPOINT.md`；
2. Airtable《执行检查点》的唯一激活记录；
3. 仓库根目录 `LAST_HANDOFF.md`；
4. 仓库根目录 `PROJECT_CURRENT.md`；
5. Airtable《当前状态》的唯一激活记录；
6. 与当前 `state_version` 绑定的 Airtable《维护日志》状态记录；
7. 再读取《维护日志》最新一条，确认没有更高未同步状态；
8. 只有任务涉及正式预测、模型研究、训练、评分、晋级或 CURRENT 修改时，才读取 ChatGPT 足球项目 File Library 中唯一文件名包含 `CURRENT_唯一正式规则` 的正式规则 CURRENT。

### 行为门

- `执行检查点`先恢复当前原子步骤；`LAST_HANDOFF`恢复上一条对话末端；`PROJECT_CURRENT` + Airtable 验证是否合法。
- 用户首条已经明确“继续/开始/执行/整改/验收”且四层一致时，直接从恢复位置继续，不要求用户重复授权。
- 首条只是“接上/看看/到哪了”时，只恢复上下文，不擅自启动新工作。
- 不得根据旧聊天摘要猜末端，也不得把较老 Rxx 当成当前阶段。

正式 CURRENT 的权威搜索域固定为 ChatGPT 足球项目 File Library；GitHub 历史文件不得替代。

## 2. 实时执行检查点协议

`ACTIVE_CHECKPOINT.md` 是稳定协议；动态检查点只保存在 Airtable《执行检查点》唯一激活记录。

### 打点粒度

只对“可恢复原子步骤”打点，不对每个只读工具调用打点。典型原子步骤包括：一场 fixture bundle、一次 PR/文件写入、一次 Actions 启动与验收、一批公开源采集、一次有外部副作用的操作。

### 开始前

在执行原子步骤前把检查点更新为 `RUNNING`，checkpoint_version +1，并写入：父任务、唯一操作ID（幂等键）、当前子步骤、上一已完成子步骤、恢复位置、目标对象、预期副作用、副作用状态、可安全重试、授权范围、禁止重复事项。

### 完成后

先核验真实结果，再更新为 `COMPLETED` 或 `WAITING`，写入真实证据（HEAD/PR/run/job/record ID/文件 SHA 等）、已完成事项、恢复位置和唯一下一步。

### 刷新/断线恢复

若看到 `RUNNING / VERIFYING`：

1. 不得直接重试；先按操作ID、目标对象和预期副作用核验外部真实状态；
2. 已发生：禁止重复执行，补写 `COMPLETED` 后继续；
3. 明确未发生且 `可安全重试=true`：允许重试同一操作ID；
4. 无法判断：设为 `BLOCKED` + `AMBIGUOUS`，停止为 `BLOCKED_CHECKPOINT_SIDE_EFFECT_AMBIGUOUS`；
5. 禁止为了保险重复创建 PR、重复写记录、重复调用有副作用或付费接口。

检查点 `state_version`：

- 等于 PROJECT_CURRENT：可恢复；
- 小于 PROJECT_CURRENT：视为陈旧，回退到 LAST_HANDOFF；
- 大于 PROJECT_CURRENT：停止为 `BLOCKED_CHECKPOINT_STATE_MISMATCH`。

检查点可在同一 state_version 下高频递增 checkpoint_version；不得因此频繁修改 PROJECT_CURRENT 或制造 main/Actions 噪声。

## 3. LAST_HANDOFF 更新协议

`LAST_HANDOFF.md` 永远只有一个当前版本。以下任一事件发生后，本轮回答结束前必须覆盖更新：

- 用户给出新的实质指令或改变方向；
- 完成一个实际步骤、PR、Actions run、实验、核验或治理动作；
- 发现新的阻塞、失败、STOP、弃权或数据缺口；
- 当前任务进入 WAITING / IN_PROGRESS / BLOCKED / COMPLETE 等新状态；
- 即将结束一轮包含实质进展的回答。

不得等对话快满才写。

至少包含：project_id、handoff_version、state_version、更新时间/状态、用户最后指令、最后实际动作、当前停点、结论/未完成事项、唯一下一步、禁止重复、branch/HEAD/PR/run/job/Artifact 等关键证据（适用时）、正式资产变化和一致性说明。

## 4. 一致性核验

必须核对：

- project_id；
- state_version；
- 执行检查点停点与 LAST_HANDOFF/PROJECT_CURRENT 是否兼容；
- 当前目标、阶段、HEAD、PR；
- 允许/禁止事项与唯一下一步；
- 更新时间；
- `PROJECT_CURRENT.md` 实算 SHA-256；
- `LAST_HANDOFF.md` 实算 SHA-256；
- Airtable 激活检查点唯一性。

`PROJECT_CURRENT.md` 的 `exact_head` 是当前被治理工作对象 HEAD，不是状态文件自身提交 HEAD。

`PROJECT_CURRENT.md` 正文的 `project_current_sha256` 固定写 `RECORDED_IN_AIRTABLE`；保存后对完整文件字节计算 SHA-256，与 Airtable 字段比对，禁止自引用。

`state_log_record_id` 固定绑定建立当前 state_version 的维护日志，不是动态 latest log。

全部一致才可标记 `HANDOFF_STATE_SYNC_VERIFIED`。

出现任一情况必须停止：执行检查点多激活、ACTIVE/LAST/PROJECT 文件缺失、Airtable 当前状态缺失、版本冲突、停点冲突、HEAD/PR/目标/权限冲突、SHA 不一致。统一为 `BLOCKED_HANDOFF_STATE_MISMATCH`；检查点副作用歧义使用专门的 `BLOCKED_CHECKPOINT_SIDE_EFFECT_AMBIGUOUS`。

## 5. Airtable 无法访问

如果当前对话没有 Airtable 访问能力：

- 可以读取 ACTIVE_CHECKPOINT / LAST_HANDOFF / PROJECT_CURRENT 恢复基本上下文；
- 必须标记 `AIRTABLE_NOT_VERIFIED`；
- 只允许回答、解释和只读盘点；
- 不得修改代码、启动研究、训练、评分、创建 PR、创建授权或实施外部操作；
- 需要执行动作时等待 Airtable 恢复或用户提供新核验证据。

阻塞：`BLOCKED_AIRTABLE_UNAVAILABLE`。不得伪造 Airtable 读取或更新结果。

## 6. 执行权限

恢复上下文不等于扩大授权。只能执行 PROJECT_CURRENT / LAST_HANDOFF / 当前用户指令明确允许的动作。

始终需要用户单独明确批准：付费接口、Secret、新盲测标签、正式训练、正式模型/权重/config/CURRENT修改、正式晋级、PR转Ready、合并PR、删除文件/历史证据、扩大研究范围或预算。

普通“继续”只恢复既有权限，不能从检查点自动获得新的独立 OOS、标签、训练、评分、晋级或付费授权。

## 7. 事实表述

PR/分支/HEAD、Actions/run/job、Artifact/SHA、训练/评分/指标、holdout、Provider/Secret、正式资产、Ready/合并、模型效果等事实必须按 `VERIFIED / REPORTED_NOT_VERIFIED / INFERRED / UNKNOWN` 表述。

禁止：把 queued 写成执行中；skipped 写成已验证；Workflow success 写成模型效果 PASS；Artifact 存在写成研究有效；技术治理 PASS 写成平局问题解决；GPT整改写成独立验收PASS；用旧HEAD证明新HEAD；用结果记录时间冒充首次标签访问；用本地测试代替准确HEAD远端证据；用转述冒充亲自核验。

## 8. 正式 CURRENT 门

四层接续与正式规则严格分离。涉及正式预测、模型研究、训练、评分、晋级或 CURRENT 修改时：

- 必须找到且仅找到一个 `CURRENT_唯一正式规则`；
- 数量不等于1：`BLOCKED_CURRENT_RULE_COUNT_MISMATCH`；
- 无法完整读取：`BLOCKED_CURRENT_RULE_UNAVAILABLE`；
- 不得自行创建、复制、重命名或选旧版冒充正式规则。

## 9. 每轮收尾

重要状态变化固定闭环：

1. 确定新的单调 state_version=N；
2. 先向 Airtable《维护日志》追加建立版本N的状态记录，取得 state_log_record_id；
3. 更新 PROJECT_CURRENT，保持 `project_current_sha256=RECORDED_IN_AIRTABLE`；
4. 更新 LAST_HANDOFF；
5. 若当前有执行中断点，更新《执行检查点》到与新 state_version 一致的 WAITING/COMPLETED/BLOCKED 状态；
6. 实算 PROJECT_CURRENT / LAST_HANDOFF SHA-256；
7. 更新 Airtable《当前状态》；
8. 回读执行检查点、LAST_HANDOFF、PROJECT_CURRENT、当前状态、绑定日志和最新日志完成核对；
9. 全部一致才输出 `HANDOFF_STATE_SYNC_VERIFIED`。

执行检查点的高频 checkpoint_version 更新不要求新增维护日志或递增 state_version；只有项目重要状态发生变化才执行上述完整闭环。

## 10. START HERE 与历史文件

Airtable START HERE 只能保存稳定启动协议，禁止写具体 Rxx、PR、HEAD、run、job、研究结论。

除 `ACTIVE_CHECKPOINT.md`、`LAST_HANDOFF.md`、`PROJECT_CURRENT.md`、Airtable《执行检查点》《当前状态》与绑定维护日志外，旧 README、旧待办、旧接续、旧交接和聊天记录全部只是历史证据，不得决定当前任务、权限或准确 HEAD。

## 11. 长任务防对话溢出协议

本节解决“一个任务还没做完，当前对话先达到上下文上限”的问题。目标不是把所有工作强行塞进一个对话，而是保证任何长任务都能在安全边界主动换对话、零重复继续。

### 11.1 长任务识别

任务出现以下任一特征时，从开始即按长任务执行：

- 明显包含多个独立阶段（例如盘点→修改→Actions→验收→状态发布）；
- 需要跨 GitHub、Airtable、File Library 或网页等多个系统；
- 预计需要反复读取长日志、Artifact、大文件或大量记录；
- 需要多轮实验、多个 workflow/run/job 或批量 fixture；
- 当前对话已经较长，继续执行可能挤压后续验收空间。

模型无法可靠读取 ChatGPT UI 的精确剩余上下文/token，因此不得等待“只剩多少token”再处理；必须使用下面的行为门主动留余量。

### 11.2 执行块

长任务必须拆成可独立恢复的“执行块”。一个执行块只完成一个明确阶段及其必要验收，例如：

- 输入盘点与裁决；
- 一次代码/治理写入；
- 一次 workflow dispatch + run/job验收；
- 一批数据处理；
- 一次状态发布闭环。

每个执行块开始前沿用现有执行检查点规则写入 `RUNNING`；执行块结束后必须先把真实结果、证据、恢复位置和唯一下一步写入 Airtable《执行检查点》，再决定是否继续下一块。

### 11.3 上下文节流

为了减少无意义上下文消耗：

- GitHub/Airtable优先使用精确文件、字段、记录、run/job和关键词查询，避免无必要读取整库、整PR历史或大段完整日志；
- 能用摘要字段、step summary、SHA、状态和关键错误定位完成核验时，不下载/展开完整日志；
- 同一事实已有可核验锚点时，不反复读取同一大对象；
- 工具返回的大段原始内容只保留必要结论和证据锚点进入接力状态，不把原文复制进维护日志或聊天回复；
- 用户更新只汇报已经得到的关键结果，不复述全部工具过程。

### 11.4 主动换对话门

出现以下任一情况时，完成当前可恢复执行块后必须主动停止继续扩展当前对话，并形成安全换对话点：

- 后续仍有一个或多个独立重阶段，而当前块已经产生大量工具输出；
- 已连续完成两个实质执行块，继续第三块并非完成当前状态闭环所必需；
- 下一步需要读取/生成明显的大日志、大Artifact、批量记录或长文件；
- 已观察到对话接近上限、回复截断、工具输出大量压缩/截断或上下文异常；
- 当前任务已经具备完整检查点，继续留在旧对话没有信息优势。

触发后必须：

1. 把检查点更新为 `WAITING` 或适当终态，明确“上一已完成子步骤 / 恢复位置 / 唯一下一步 / 禁止重复”；
2. 如本轮产生重要项目状态变化，先按第9节完成状态发布；没有重要状态变化则只更新实时检查点，不人为递增state_version；
3. 必要时更新 `LAST_HANDOFF.md`；
4. 给用户一个简短回执：`已到安全换对话点；新建对话只需说“继续”，无需重复任务。`；
5. 禁止在已经具备安全断点后为了“这一条必须做完”继续堆大量工具调用。

### 11.5 新对话恢复

新对话收到“继续/开始”后，严格按第1节启动顺序读取实时检查点；若检查点与四层状态一致，直接执行“唯一下一步”，不得要求用户重新描述长任务，也不得重跑“已完成事项”。

该协议只改变任务分段与上下文使用方式，不扩大任何研究、标签、训练、Provider、PR、合并或正式资产权限。
