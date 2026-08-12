# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 1
- updated_at_utc: 2026-08-12T03:52:00Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_GITHUB_AIRTABLE_FILE_LIBRARY_GOVERNANCE
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态

- status: WAITING
- current_phase: CONTEXT_GOVERNANCE_WAITING_USER_ACCEPTANCE
- current_objective: 完成新对话永久接续治理并等待用户验收治理Draft PR；不启动任何足球研究。
- exact_head: 06a0825308573420e4c0096c157d52adf7b8d7d2
- branch: research/v520-public-web-forward-pit-r44a
- pull_request: #176
- pull_request_state: VERIFIED_OPEN_DRAFT_UNMERGED
- formal_weight: 0
- governance_branch: governance/permanent-conversation-continuity-v1
- governance_pull_request: #177
- governance_pull_request_state: VERIFIED_OPEN_DRAFT_UNMERGED
- governance_head: VERIFIED_EXTERNALLY_NOT_SELF_REFERENTIAL

`exact_head` / `branch` / `pull_request` 表示冻结的被治理工作对象，不表示本治理文件所在 Git 提交。治理 PR 的最终 HEAD 必须从 GitHub 实时核验，禁止写入本文件造成自引用。

## 当前证据

- verified_facts: GitHub main 为 f3a6450087c7f532da05056db6c21ee3c658a9e9；active work PR #176 为 Open/Draft/未合并且 head=06a0825308573420e4c0096c157d52adf7b8d7d2；governance PR #177 为 Open/Draft/未合并；本次只读核验时 Actions in_progress=0、queued=0；Airtable《当前状态》只有一条记录；File Library 中正式规则 CURRENT 数量=1。
- reported_not_verified: PR #176 正文、旧 Airtable 历史日志及旧聊天中关于历史 run/job/Artifact、研究指标、技术验收和科学效果的陈述，除非在新对话中重新按准确身份实时核验。
- inferred_facts: 无。
- unknown_facts: 正式 CURRENT 原始 DOCX 字节 SHA-256 当前未取得可计算字节，因此不得编造；未逐项实时复核的历史 Artifact/指标状态均为 UNKNOWN 或 REPORTED_NOT_VERIFIED。
- workflow_runs: VERIFIED 当前 in_progress=0；VERIFIED 当前 queued=0；本治理未启动任何 workflow。
- job_ids: NONE_EXECUTED_BY_THIS_GOVERNANCE
- artifact_ids: NONE_CREATED_BY_THIS_GOVERNANCE
- artifact_sha256: NOT_APPLICABLE
- evidence_observed_at: 2026-08-12T03:52:00Z

## 允许事项

- 只读核验 `AGENTS.md`、`PROJECT_CURRENT.md`、Airtable同步、治理PR #177差异与验证结果。
- 用户明确要求时，可仅修复接续治理缺陷；任何重要状态变化必须递增 `state_version` 并重新闭环同步。

## 禁止事项

- 禁止足球研究或研究重跑、训练、评分、调参。
- 禁止读取新的盲测/holdout标签。
- 禁止访问 Provider、API-Football、付费接口，禁止使用或创建 Secret。
- 禁止创建研究授权文件。
- 禁止修改正式模型、正式数据、config、正式预测逻辑或正式 CURRENT 正文。
- 禁止执行冻结背景工作对象 PR #176 的任何研究动作。
- 禁止将任何 PR 转 Ready，禁止合并 PR #177 或其他 PR。
- 禁止删除、覆盖或改写历史证据与旧维护日志。
- 禁止把 Workflow success、Artifact 存在或 GPT 报告解释为模型有效或科学 PASS。

## 唯一下一步

- 用户验收治理 Draft PR #177；验收前不继续足球研究、不转 Ready、不合并。

## 停止条件

- `PROJECT_CURRENT.md` 与 Airtable《当前状态》的 `state_version`、当前目标、`exact_head`、PR、允许事项、禁止事项、唯一下一步或 SHA-256 任一不一致：`BLOCKED_STATE_MISMATCH`。
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

本次任务是接续治理，不涉及正式预测或模型操作，因此不修改正式规则正文，也不从 CURRENT 获得任何新增研究授权。

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: recADs7Ppjt0bLMde
- airtable_state_version: 1
- airtable_sync_status: STATE_SYNC_VERIFIED
