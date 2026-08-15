# 足球项目新对话与中断恢复规则

本文件只保存长期稳定的接续治理，不记录动态 Rxx、PR、HEAD、run、Artifact 或研究结论。

目标：新对话能零重述接续，同时避免启动阶段扫描大量旧聊天、维护日志、PR、Artifact 或项目文件。

## 1. FAST_BOOTSTRAP：默认新对话启动

正常新对话、刷新或断线恢复，默认只做三个小读取：

1. Airtable Base《足球项目接续》→《当前状态》唯一激活记录，只取接续必要字段：`project_id / state_version / main SHA / 执行检查点记录ID / 执行检查点版本 / 状态 / 当前阻塞 / 唯一下一步`；
2. 按《当前状态》里的“执行检查点记录ID”精确读取那一条《执行检查点》，禁止为了找断点先列整表；
3. 只核验 GitHub `main` 当前 HEAD SHA，不在默认启动阶段读取整个仓库文件。

满足以下四项即视为 `FAST_BOOTSTRAP_PASS`：

- `project_id == football-project`；
- 当前状态与检查点 `state_version` 一致；
- 当前状态记录的 checkpoint_version 与该检查点一致；
- GitHub 真实 `main` HEAD == 当前状态记录的 `main SHA`。

通过后：

- 用户只是问“到哪了/接上了吗/看看状态”：直接依据当前状态+检查点回答，不展开历史；
- 用户说“继续/开始/接着干/执行”：从检查点的“恢复位置/唯一下一步”继续，不要求重述，也不重做“已完成事项”；
- 若当前任务属于正式预测、模型研究、训练、评分、晋级、正式概率/比分/EV或 CURRENT 修改，仍必须在实际执行前按第8节读取唯一正式 CURRENT；FAST_BOOTSTRAP 不绕过 CURRENT。

### 1.1 什么时候才扩大读取

仅出现以下情况之一，才进入 `EXPANDED_VERIFY`：

- GitHub `main` HEAD 与 Airtable 保存 SHA 不一致；
- project_id / state_version / checkpoint_version 不一致；
- 检查点为 `RUNNING / VERIFYING`，需要核验外部副作用；
- 当前任务明确需要某个仓库文件、PR、run/job、Artifact 或历史研究证据；
- 正在发布新 state_version、做状态修复或完整治理验收。

扩大读取时仍必须按需精确取证：优先读当前任务直接需要的 `LAST_HANDOFF.md`、`PROJECT_CURRENT.md`、绑定维护日志、指定 PR/run/job/Artifact；禁止无原因整库、整表、整PR历史扫描。

### 1.2 启动阶段明确禁止

- 禁止把“搜索全部项目聊天”作为接续步骤；
- 禁止启动时列出全部维护日志；
- 禁止启动时列出全部 PR / Actions / Artifact；
- 禁止遍历整个 File Library 或整个仓库寻找“可能相关”的旧文件；
- 禁止因用户只说“继续”而回放历史 Rxx。

旧聊天、旧文件、旧 README、旧接续和记忆只能按需作为历史证据，不能决定当前断点。

## 2. 实时执行检查点协议

动态断点只保存在 Airtable《执行检查点》唯一激活记录；`ACTIVE_CHECKPOINT.md` 是稳定协议，不保存动态子步骤。

只对可恢复原子步骤打点，不对每个只读调用打点。原子步骤包括：一次文件/PR写入、一次 workflow dispatch+验收、一批数据处理、一次状态发布、一次可能产生外部副作用的操作。

### 开始前

将检查点更新为 `RUNNING`，checkpoint_version +1，并至少保存：父任务、操作ID（幂等键）、当前子步骤、上一已完成子步骤、恢复位置、目标对象、预期副作用、副作用状态、可安全重试、授权范围、禁止重复、唯一下一步。

### 完成后

先核验真实结果，再更新为 `COMPLETED / WAITING / BLOCKED`，记录真实证据、已完成事项、恢复位置和唯一下一步。

### RUNNING / VERIFYING 恢复

1. 不得直接重试；先按操作ID+目标对象+预期副作用查真实状态；
2. 已发生：禁止重复，补写完成状态；
3. 明确未发生且可安全重试：只允许重试同一操作ID；
4. 无法判断：`BLOCKED_CHECKPOINT_SIDE_EFFECT_AMBIGUOUS`；
5. 禁止“为了保险”重复创建PR、重复写记录、重复付费/有副作用调用。

## 3. LAST_HANDOFF / PROJECT_CURRENT 分工

- Airtable《执行检查点》：实时、细粒度、优先恢复；
- `LAST_HANDOFF.md`：对话末端与重要接力说明；
- `PROJECT_CURRENT.md`：重要项目/研究状态与 state_version；
- Airtable《当前状态》：结构化当前权威入口；
- Airtable《维护日志》：追加式历史证据。

FAST_BOOTSTRAP 正常通过时，不要求每个新对话都全文读取 LAST_HANDOFF / PROJECT_CURRENT；它们在 `EXPANDED_VERIFY`、状态发布、状态修复或当前任务确实需要时读取。

发生实质进展、方向变化、阻塞变化或重要治理动作时，应及时更新接力状态，不等待对话快满。

## 4. 分级一致性核验

### 4.1 快速一致性（默认启动）

只核对：

- project_id；
- state_version；
- checkpoint_version；
- GitHub main 真实 HEAD 与 Airtable `main SHA`。

四项一致即可 FAST_BOOTSTRAP_PASS。

### 4.2 完整一致性（仅必要时）

以下场景必须做完整核验：新 state_version 发布、状态修复、main SHA漂移、接力冲突、PR合并前治理验收、检查点副作用歧义。

完整核验包括：

- `LAST_HANDOFF.md` / `PROJECT_CURRENT.md` 内容与 state_version；
- 当前目标、HEAD/PR、权限、唯一下一步与禁止事项；
- `PROJECT_CURRENT.md` / `LAST_HANDOFF.md` / `ACTIVE_CHECKPOINT.md` SHA-256（需要时）；
- 当前 state_version 绑定维护日志；
- Airtable 激活检查点唯一性。

出现真实冲突停止为 `BLOCKED_HANDOFF_STATE_MISMATCH`；副作用歧义使用 `BLOCKED_CHECKPOINT_SIDE_EFFECT_AMBIGUOUS`。

## 5. Airtable 无法访问

若 Airtable 不可访问：

- 可精确读取 GitHub `LAST_HANDOFF.md` / `PROJECT_CURRENT.md` 恢复只读上下文；
- 标记 `AIRTABLE_NOT_VERIFIED`；
- 只允许回答、解释、只读盘点；
- 不得修改代码、启动研究/训练/评分、创建PR或执行外部副作用；
- 需要写操作时停止为 `BLOCKED_AIRTABLE_UNAVAILABLE`。

## 6. 执行权限

恢复上下文不等于扩大授权。只能执行当前用户指令与已记录授权范围允许的动作。

始终需要用户单独明确批准：付费接口、Secret、新盲测标签、正式训练、正式模型/权重/config/CURRENT修改、正式晋级、PR转Ready、合并PR、删除历史证据、扩大研究范围或预算。

普通“继续”只恢复既有权限。

## 7. 事实表述

PR/HEAD、Actions/run/job、Artifact/SHA、训练/评分、holdout、Provider/Secret、正式资产、Ready/合并、模型效果等事实必须按 `VERIFIED / REPORTED_NOT_VERIFIED / INFERRED / UNKNOWN` 表述。

禁止把 queued 写成执行中、skipped 写成已验证、Workflow success 写成科学PASS、Artifact存在写成研究有效、技术治理PASS写成平局已解决、GPT整改写成独立验收PASS、旧HEAD证明新HEAD、转述冒充亲自核验。

## 8. 正式 CURRENT 门

接续状态与正式规则严格分离。涉及正式预测、模型研究、训练、评分、晋级、正式概率/比分/EV或 CURRENT 修改时：

- 必须在 ChatGPT 足球项目 File Library / 当前项目来源中找到且只找到一个文件名包含 `CURRENT_唯一正式规则` 的正式规则；
- 数量不等于1：`BLOCKED_CURRENT_RULE_COUNT_MISMATCH`；
- 无法完整读取：`BLOCKED_CURRENT_RULE_UNAVAILABLE`；
- 必须在实际模型/研究执行前完整读取 CURRENT；
- GitHub历史文件、旧聊天、研究PR不得替代 CURRENT。

## 9. state_version 发布闭环

只有重要项目状态变化才递增 state_version；高频 checkpoint_version 不得制造 state 噪声。

新 state_version 固定闭环：

1. 检查点进入 RUNNING；
2. Airtable《维护日志》追加建立新 state 的绑定记录；
3. 更新 `PROJECT_CURRENT.md`；
4. 更新 `LAST_HANDOFF.md`；
5. 更新与新 state 一致的执行检查点；
6. 计算需要记录的文件 SHA-256；
7. 更新 Airtable《当前状态》的 state_version、真实 main SHA、研究HEAD、绑定日志ID、文件SHA和checkpoint_version；
8. 完整回读 GitHub + Airtable；
9. 全部一致才可输出 `HANDOFF_STATE_SYNC_VERIFIED`。

科学 PASS/FAIL/STOP、研究瓶颈/主方向改变、新 research HEAD/PR/run/Artifact成为恢复锚点、标签/PIT门变化等，必须先完成新 state_version 双边发布，再开始下一项实质研究。

## 10. START_HERE 与历史研究检索

START_HERE 只保存稳定路由，不得写死当前 Rxx、PR、HEAD、run/job、state_version、checkpoint_version或研究结论。

历史问题使用“按需索引”，不得全量加载：

1. 用户问某个 Rxx：先按 Rxx 精确搜索 Airtable《维护日志》；
2. 用户问某类机制：用1—3个关键词定向搜索维护日志；
3. 命中后只打开最相关记录，再按其中 PR/HEAD/run/Artifact 精确取 GitHub 证据；
4. 除非用户明确要求全量审计，禁止列出245+日志或所有PR来回答单个历史问题。

未被当前 GitHub/Airtable状态链绑定的 Library 孤立产物不得自动升级为项目进展。

## 11. 长任务防对话溢出

长任务包括多阶段、跨系统、大日志/Artifact、批量数据、多run/job或当前对话已明显很长的任务。

### 11.1 执行块

拆成可独立恢复的执行块；一个块只完成一个明确阶段及必要验收。每块开始前打 RUNNING 检查点，结束后先写真实证据、恢复位置和唯一下一步，再决定是否继续。

### 11.2 上下文节流

- 优先精确文件、字段、record ID、run/job、关键词查询；
- 能用摘要字段/SHA/关键错误核验时，不展开完整大对象；
- 同一事实已有锚点时不反复读取；
- 大段原文不复制进聊天或维护日志，只保存结论和证据锚点。

### 11.3 主动换对话门

出现任一情况，在当前可恢复块完成后形成安全换对话点：

- 已连续完成两个实质执行块；
- 下一步是明显的大日志、大Artifact、批量处理或另一独立重阶段；
- 回复/工具输出出现截断、压缩或上下文异常；
- 已有完整检查点，继续留在旧对话没有信息优势。

触发后：检查点写为 WAITING/适当终态，保存“上一已完成/恢复位置/唯一下一步/禁止重复”；重要状态变化先完成state发布；然后提示用户新建对话只需说“继续”。

### 11.4 新对话恢复

新对话收到“继续/开始”时先执行第1节 FAST_BOOTSTRAP；通过后直接从唯一检查点继续。不得要求用户重述长任务，不得重跑已完成事项。

本协议只优化接续与上下文使用，不改变 CURRENT、模型、数据、研究预注册或任何正式权限。