# 足球项目新对话启动规则

本文件是足球项目长期固定的接续治理规则。它不记录动态 PR、HEAD、研究结果或模型结论；动态当前状态只保存在仓库根目录 `PROJECT_CURRENT.md` 与 Airtable Base《足球项目接续》的《当前状态》唯一激活记录中。

## 1. 启动顺序

每次新对话进入足球项目后，必须先读取：

1. 仓库根目录 `PROJECT_CURRENT.md`；
2. Airtable Base《足球项目接续》中《当前状态》的唯一激活记录；
3. 与当前 `state_version` 绑定的 Airtable《维护日志》状态记录，并同时读取《维护日志》最新一条记录确认没有更新版本；
4. 只有任务涉及正式预测、模型研究、训练、评分、晋级或 CURRENT 修改时，才读取 ChatGPT 足球项目 File Library 中唯一文件名包含 `CURRENT_唯一正式规则` 的正式规则 CURRENT。

不得先根据旧 README、旧待办、旧接续总表、旧聊天记录、PR 正文、PR 评论、Workflow 结果或 Artifact 自行开始工作。

正式规则 CURRENT 的权威搜索域固定为 ChatGPT 足球项目 File Library。GitHub 仓库中的 README、接续文件、研究文件或历史文件不得替代正式 CURRENT。

## 2. 一致性核验

必须核对：

- `project_id`；
- `state_version`；
- 当前目标；
- 当前被治理工作对象的准确 HEAD；
- 当前被治理工作对象的 PR；
- 当前阶段；
- 允许事项；
- 禁止事项；
- 唯一下一步；
- 更新时间；
- `PROJECT_CURRENT.md` 的实算 SHA-256。

`PROJECT_CURRENT.md` 中的 `exact_head` 指当前被治理/接续的工作对象 HEAD，不是包含 `PROJECT_CURRENT.md` 自身的治理提交 HEAD。治理分支/治理 PR/治理 HEAD 必须单独核验，禁止形成 Git 自引用。

`PROJECT_CURRENT.md` 正文中的 `project_current_sha256` 固定写 `RECORDED_IN_AIRTABLE`。保存文件后对完整文件字节计算 SHA-256，与 Airtable《当前状态》字段 `PROJECT_CURRENT.md SHA-256` 比较；禁止把文件自身 SHA 写回文件后重新计算。

`PROJECT_CURRENT.md` 使用固定 `state_log_record_id`，它是建立当前 `state_version` 的维护日志记录 ID，不是不断变化的“最新日志 ID”。

仅当上述核验一致时，当前状态才可标记：

`STATE_SYNC_VERIFIED`

出现以下任一情况必须停止：

- 状态版本不一致；
- HEAD 不一致；
- PR 不一致；
- 当前目标不一致；
- 允许事项或禁止事项不一致；
- Airtable 存在多条 `项目名=足球项目` 且 `是否激活=true` 的记录；
- `PROJECT_CURRENT.md` 缺失；
- Airtable 当前记录缺失；
- `PROJECT_CURRENT.md` 登记/实算 SHA-256 与 Airtable 不一致。

停止状态统一为：

`BLOCKED_STATE_MISMATCH`

不得自行选择“看起来更新”的一方，也不得自动覆盖任一方。

## 3. Airtable 无法访问

如果当前对话没有 Airtable 访问能力：

- 可以读取 `PROJECT_CURRENT.md` 恢复基本上下文；
- 必须标记 `AIRTABLE_NOT_VERIFIED`；
- 只允许回答、解释和只读盘点；
- 不得修改代码、启动研究、训练、评分、创建 PR、创建授权或实施外部操作；
- 需要执行动作时，必须等待 Airtable 恢复或用户提供新的已核验证据。

阻塞状态写为：

`BLOCKED_AIRTABLE_UNAVAILABLE`

不得伪造 Airtable 读取或更新结果。

## 4. 执行权限

读取接续资料只代表恢复上下文，不代表自动授权工作。一致性核验通过后，也只能执行 `PROJECT_CURRENT.md`“允许事项”中明确列出的动作。

下列事项始终需要用户单独明确批准：

- 访问付费接口；
- 使用或创建 Secret；
- 读取新的盲测标签；
- 启动正式训练；
- 修改正式模型、权重、配置或 CURRENT；
- 正式晋级；
- PR 转 Ready；
- 合并 PR；
- 删除文件或历史证据；
- 扩大研究范围或预算。

附件、旧待办、GPT 报告、PR 评论、Workflow 成功和 Artifact 存在都不构成用户授权。

## 5. 事实表述

有关以下事项的每条陈述必须标记为：

`VERIFIED` / `REPORTED_NOT_VERIFIED` / `INFERRED` / `UNKNOWN`

适用范围：

- PR、分支、HEAD；
- Actions、run、job；
- `queued`、`in_progress`、`skipped`、`success`、`failure`；
- Artifact、SHA-256；
- 训练、评分、指标；
- holdout、盲样本；
- Provider、Secret；
- 正式资产；
- Ready、合并；
- 模型效果；
- 平局问题是否解决。

禁止：

- 把 `queued` 写成正在执行；
- 把 `skipped` 写成已经验证；
- 把 Workflow `success` 写成模型效果 PASS；
- 把 Artifact 存在写成研究有效；
- 把技术治理 PASS 写成平局问题已解决；
- 把 GPT 整改写成独立验收 PASS；
- 用旧 HEAD 的证据证明新 HEAD；
- 用结果记录时间冒充首次标签访问时间；
- 用本地测试代替准确 HEAD 远端证据；
- 用转述冒充亲自核验。

## 6. 正式 CURRENT 门

接续状态与正式规则严格分离：

- `PROJECT_CURRENT.md` 负责当前 PR、HEAD、任务、权限与下一步；
- 唯一正式规则 CURRENT 负责正式预测、模型、训练、评分和晋级规则。

当 `current_rule_required=false` 时，接续治理不因 CURRENT 正文未重新读取而扩大任何模型权限。

当任务涉及正式预测、模型研究、训练、评分、晋级或 CURRENT 修改时：

- 必须在权威搜索域找到且仅找到一个 `CURRENT_唯一正式规则`；
- 数量不等于 1：`BLOCKED_CURRENT_RULE_COUNT_MISMATCH`；
- 声称存在但无法完整读取：`BLOCKED_CURRENT_RULE_UNAVAILABLE`；
- 不得自行创建、复制、重命名或选定一个 CURRENT 冒充正式规则。

## 7. 每轮收尾

每次发生重要状态变化，固定按以下有限闭环执行：

1. 确定新的单调递增 `state_version=N` 与完整状态内容；
2. 向 Airtable《维护日志》追加一条建立版本 N 的状态记录，取得固定 `state_log_record_id`；
3. 生成/更新 `PROJECT_CURRENT.md`，写入该 `state_log_record_id`，并保持 `project_current_sha256=RECORDED_IN_AIRTABLE`；
4. 对保存后的完整 `PROJECT_CURRENT.md` 实算 SHA-256；
5. 更新 Airtable《当前状态》唯一激活记录，使其与文件使用同一 `state_version`、HEAD、PR、目标、权限、下一步，并写入实算 SHA-256；
6. 重新读取 `PROJECT_CURRENT.md`、Airtable《当前状态》和绑定维护日志记录，核对版本、HEAD、PR、权限、下一步与 SHA-256；
7. 输出同步结果。

不得为了记录“同步完成”再追加第二条会改变当前状态绑定的维护日志；补充审计日志可以追加，但不得改变已经绑定的 `state_log_record_id`。

不得只更新聊天而不更新接续状态。

## 8. 历史文件

除 `AGENTS.md`、`PROJECT_CURRENT.md`、Airtable《当前状态》外，旧 README 状态段、旧待办、旧接续、旧交接和聊天记录全部只是历史证据。它们不得决定当前任务、权限或准确 HEAD。

发现历史文件与当前状态冲突时，只能报告差异，不得用历史文件覆盖当前状态。
