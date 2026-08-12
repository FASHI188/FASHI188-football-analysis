# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 5
- updated_at_utc: 2026-08-12T04:34:00Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_GITHUB_AIRTABLE_FILE_LIBRARY_GOVERNANCE
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态

- status: WAITING
- current_phase: CONTINUITY_GOVERNANCE_ACTIVE_ON_MAIN_WAITING_USER_DIRECTION
- current_objective: 接续治理PR #177已合并并在main生效；冻结背景工作对象PR #176保持不执行；等待用户下一步指令。若继续足球研究，必须先重新执行唯一正式CURRENT启动协议并实时核验#176。
- exact_head: 06a0825308573420e4c0096c157d52adf7b8d7d2
- branch: research/v520-public-web-forward-pit-r44a
- pull_request: #176
- pull_request_state: VERIFIED_OPEN_DRAFT_UNMERGED
- formal_weight: 0
- governance_branch: governance/permanent-conversation-continuity-v1
- governance_pull_request: #177
- governance_pull_request_state: VERIFIED_CLOSED_MERGED
- governance_head: 21a88cde05a30b79413b68ca1d193c792495fe68
- governance_merge_sha: 5cef27148d8f82fcfcfa68dc635afa19c5763dce

`exact_head` / `branch` / `pull_request` 表示冻结的被治理工作对象。治理PR #177已经合并；`governance_head`表示该PR最终被合并的源HEAD，`governance_merge_sha`表示合并提交，均不是本文件自身提交，因此不构成Git自引用。

## 当前证据

- verified_facts: 用户已明确授权合并治理PR #177；GitHub实时确认 #177 已 closed/merged=true，最终源HEAD=21a88cde05a30b79413b68ca1d193c792495fe68，merge SHA=5cef27148d8f82fcfcfa68dc635afa19c5763dce；main 已包含 `AGENTS.md`、`PROJECT_CURRENT.md`、`governance/validate_project_continuity.py`；active work PR #176 仍为 Open/Draft/未合并且 head=06a0825308573420e4c0096c157d52adf7b8d7d2。
- reported_not_verified: PR #176 正文、旧 Airtable 历史日志及旧聊天中关于历史 run/job/Artifact、研究指标、技术验收和科学效果的陈述，除非按当前准确身份重新实时核验。
- inferred_facts: 无。
- unknown_facts: 正式 CURRENT 原始 DOCX 字节 SHA-256 当前未取得可计算字节，因此不得编造；未逐项实时复核的历史 Artifact/指标状态均为 UNKNOWN 或 REPORTED_NOT_VERIFIED。
- workflow_runs: 本轮未启动任何足球研究 workflow；治理合并不等于模型/研究执行。
- job_ids: NONE_EXECUTED_BY_THIS_GOVERNANCE
- artifact_ids: NONE_CREATED_BY_THIS_GOVERNANCE
- artifact_sha256: NOT_APPLICABLE
- evidence_observed_at: 2026-08-12T04:34:00Z

## 允许事项

- 只读核验 `AGENTS.md`、`PROJECT_CURRENT.md`、Airtable同步、治理PR #177合并状态以及冻结背景工作对象PR #176身份。
- 回答用户关于项目现状、下一步和治理状态的问题。
- 用户明确要求继续足球研究时，允许先执行启动协议：重新检索并完整读取唯一正式CURRENT，输出启动回执，实时核验PR #176及必要仓库/Airtable状态；只有启动门通过并且当前权限允许时，才能决定后续执行动作。
- 任何重要状态变化必须递增 `state_version` 并重新闭环同步。

## 禁止事项

- 在未重新通过正式CURRENT启动协议前，禁止执行PR #176或其他足球研究、研究重跑、训练、评分、调参。
- 禁止读取新的盲测/holdout标签。
- 禁止访问 Provider、API-Football、付费接口，禁止使用或创建 Secret，除非用户另行明确授权且正式规则允许。
- 禁止创建研究授权文件，除非用户另行明确授权且正式规则允许。
- 禁止修改正式模型、正式数据、config、正式预测逻辑或正式 CURRENT 正文，除非用户另行明确授权且正式规则允许。
- 禁止将治理合并、Workflow success、Artifact存在或GPT报告解释为模型有效或科学PASS。
- 禁止未经单独明确授权合并其他PR、删除文件或改写历史证据。

## 唯一下一步

- 等待用户下一步指令；若用户要求继续足球研究，第一步固定为重新检索并完整读取唯一正式CURRENT、输出启动回执并实时核验冻结背景工作对象PR #176，未通过前不得启动研究。

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

当前任务已完成接续治理合并，不涉及正式预测或模型执行。下一次用户要求继续足球研究时，必须重新执行正式CURRENT启动协议，本文件不得替代正式规则正文。

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: rec5gUhfZvCj3XUEO
- airtable_state_version: 5
- airtable_sync_status: STATE_SYNC_VERIFIED
