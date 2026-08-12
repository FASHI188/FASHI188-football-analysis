# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 4
- updated_at_utc: 2026-08-12T04:28:00Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_GITHUB_AIRTABLE_FILE_LIBRARY_GOVERNANCE
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态

- status: WAITING
- current_phase: CONTEXT_GOVERNANCE_READY_AWAITING_MERGE_AUTHORIZATION
- current_objective: 治理PR #177 已按用户独立授权转Ready；保持Open/Ready/未合并，等待用户单独明确授权合并；不启动任何足球研究。
- exact_head: 06a0825308573420e4c0096c157d52adf7b8d7d2
- branch: research/v520-public-web-forward-pit-r44a
- pull_request: #176
- pull_request_state: VERIFIED_OPEN_DRAFT_UNMERGED
- formal_weight: 0
- governance_branch: governance/permanent-conversation-continuity-v1
- governance_pull_request: #177
- governance_pull_request_state: VERIFIED_OPEN_READY_UNMERGED
- governance_head: VERIFIED_EXTERNALLY_NOT_SELF_REFERENTIAL

`exact_head` / `branch` / `pull_request` 表示冻结的被治理工作对象，不表示本治理文件所在 Git 提交。治理 PR 的最终 HEAD 必须从 GitHub 实时核验，禁止写入本文件造成自引用。

## 当前证据

- verified_facts: 用户已明确授权将治理PR #177转Ready；GitHub实时执行后 #177 为 Open/Ready/未合并（draft=false）且转Ready时治理HEAD=3c7e6346e9df5f73252847b6b1e12a0da87b150e；active work PR #176 继续作为冻结背景对象，head=06a0825308573420e4c0096c157d52adf7b8d7d2；Airtable《当前状态》保持唯一足球项目激活记录；正式规则 CURRENT 数量=1。
- reported_not_verified: PR #176 正文、旧 Airtable 历史日志及旧聊天中关于历史 run/job/Artifact、研究指标、技术验收和科学效果的陈述，除非在新对话中重新按准确身份实时核验。
- inferred_facts: 无。
- unknown_facts: 正式 CURRENT 原始 DOCX 字节 SHA-256 当前未取得可计算字节，因此不得编造；未逐项实时复核的历史 Artifact/指标状态均为 UNKNOWN 或 REPORTED_NOT_VERIFIED。
- workflow_runs: 本治理未启动任何研究 workflow；治理HEAD的PR触发workflow需实时核验，不得用无结果自动解释为PASS。
- job_ids: NONE_EXECUTED_BY_THIS_GOVERNANCE
- artifact_ids: NONE_CREATED_BY_THIS_GOVERNANCE
- artifact_sha256: NOT_APPLICABLE
- evidence_observed_at: 2026-08-12T04:28:00Z

## 允许事项

- 只读核验 `AGENTS.md`、`PROJECT_CURRENT.md`、Airtable同步、治理PR #177差异、Ready状态与验证结果。
- 用户明确要求时，可仅修复接续治理缺陷；任何重要状态变化必须递增 `state_version` 并重新闭环同步。
- 只有用户后续单独明确授权合并治理PR #177时，才允许在合并前实时复核准确HEAD、PR状态与必要检查，并执行合并；合并本身必须形成新的状态版本并闭环同步。

## 禁止事项

- 禁止足球研究或研究重跑、训练、评分、调参。
- 禁止读取新的盲测/holdout标签。
- 禁止访问 Provider、API-Football、付费接口，禁止使用或创建 Secret。
- 禁止创建研究授权文件。
- 禁止修改正式模型、正式数据、config、正式预测逻辑或正式 CURRENT 正文。
- 禁止执行冻结背景工作对象 PR #176 的任何研究动作。
- 未获得用户单独明确授权前，禁止合并治理PR #177或其他PR。
- 禁止删除、覆盖或改写历史证据与旧维护日志。
- 禁止把 Workflow success、Artifact 存在或 GPT 报告解释为模型有效或科学 PASS。

## 唯一下一步

- 用户单独明确授权合并治理PR #177；未获得该独立授权前保持Open/Ready/未合并，不启动足球研究。

## 停止条件

- `PROJECT_CURRENT.md` 与 Airtable《当前状态》的 `project_id`、`state_version`、`updated_at_utc`、状态、当前阶段、当前目标、`exact_head`、分支、PR、PR状态、允许事项、禁止事项、唯一下一步、停止条件、绑定日志ID或 SHA-256 任一不一致：`BLOCKED_STATE_MISMATCH`。
- Airtable 无法读取：`BLOCKED_AIRTABLE_UNAVAILABLE`，只允许回答、解释和只读盘点。
- Airtable 中 `项目名=足球项目` 且 `是否激活=true` 的记录数量不等于1：`BLOCKED_STATE_MISMATCH`。
- 涉及正式预测/模型研究/训练/评分/晋级/CURRENT修改时，正式规则 CURRENT 数量不等于1：`BLOCKED_CURRENT_RULE_COUNT_MISMATCH`；宣称存在但无法完整读取：`BLOCKED_CURRENT_RULE_UNAVAILABLE`。
- 发现当前工作对象身份无法实时核验或与本文件冲突时立即停止，不得猜测更新的一方。

## 正式规则状态

- current_rule_required: false
- current_rule_count: 1
- current_rule_identity: 足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx
- current_rule_sha256: UNKNOWN_RAW_BYTES_NOT_AVAILABLE
- current_rule_status: VERIFIED_IDENTITY_AND_COUNT_ONLY

本次任务是接续治理状态变更，不涉及正式预测或模型操作，因此不修改正式规则正文，也不从 CURRENT 获得任何新增研究授权。

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: rec3RyCRyB4NNoXKZ
- airtable_state_version: 4
- airtable_sync_status: STATE_SYNC_VERIFIED
