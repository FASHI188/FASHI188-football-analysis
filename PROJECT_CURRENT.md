# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 15
- updated_at_utc: 2026-08-13T05:13:25Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_GITHUB_EXACT_HEAD_ACTIONS
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态（最高优先级）

- status: IN_PROGRESS
- task: GitHub应用与Actions质量安全整改
- completed_steps: 前三个GitHub App已由用户安装；仓库workflow/治理入口已盘点；无需账号授权/无需Secret的质量安全guard已实现；独立Draft PR已建立；exact-HEAD Actions已通过
- current_step: PR #186 已实现并通过exact-HEAD质量安全检查，等待用户决定是否授权合并
- next_action: 保持PR #186 Draft/Open/未合并；仅在用户明确授权后合并并关闭本IN_PROGRESS任务
- source_thread: 足球研究进展
- started_at: 2026-08-13T13:02:00+08:00
- exact_head: 7b213f3b623d74000165304b237d5190b65c8e3c
- branch: chore/github-actions-quality-security-r1
- pull_request: #186
- pull_request_state: Draft/Open/未合并
- acceptance: IMPLEMENTED_ACTIONS_PASS_AWAITING_USER_MERGE_AUTHORIZATION
- formal_model_change: 0
- formal_data_change: 0
- config_change: 0
- CURRENT_change: 0

## exact-HEAD Actions证据

- quality_security_run: 31669557867
- quality_security_job: 94351192037
- quality_security_result: completed/success
- quality_security_checks: git diff --check PASS；6/6 unit tests PASS；changed-file guard PASS
- repository_integrity_run: 31669557872
- repository_integrity_result: completed/success
- first_failed_run: 31669501916
- first_failed_reason: guard源码自身含用于检测的冲突标记字面量，导致自匹配；已在exact_head修复，未隐瞒失败
- workflow_permissions_observed: Contents=read；Metadata=read
- paid_service: 0
- extra_service_account: 0
- repository_secret_reference: 0

## 新对话启动硬门

- 新对话启动时必须先读取本文件和Airtable《足球项目接续》当前状态。
- 只要存在 `status=IN_PROGRESS`，必须优先续做该任务。
- `IN_PROGRESS` 未关闭前，禁止按旧研究“唯一下一步”另开研究路线。
- 用户下达“开始处理”后，必须先写入进行中任务到 Airtable 当前状态、维护日志和 PROJECT_CURRENT，再允许开始实际工作。
- 进行中任务必须至少记录：task、completed_steps、current_step、next_action、source_thread、started_at、exact_head。
- exact_head 未经实时核验时必须写 `UNKNOWN`，不得用旧聊天记忆冒充实时HEAD。

## 本任务已实现的工程护栏

- `.github/workflows/football-engineering-quality-security.yml`：独立PR/push/manual质量安全workflow，顶层仅`contents: read`。
- `scripts/governance/quality_security_guard.py`：仅检查当前diff，避免280个历史workflow遗留债务把所有新PR强制打红。
- `scripts/governance/test_quality_security_guard.py`：6项针对权限、Secret引用、危险shell、私钥标记及自合规的单元测试。
- 检查范围包括changed-line whitespace、Python语法、冲突标记、高信号Secret标记、remote-pipe-to-shell、chmod 777、新workflow权限、`pull_request_target`、`write-all`及本workflow自我只读/无Secret约束。
- 仅使用`actions/checkout`与`actions/setup-python`，不接Codecov或其他需要额外服务账号的第三方覆盖率服务。

## 本任务允许事项

- 只读复核PR #186、exact-HEAD Actions与diff。
- 用户明确授权后合并PR #186，并在合并后更新Airtable、维护日志和PROJECT_CURRENT关闭IN_PROGRESS。

## 本任务禁止事项

- 禁止未获用户明确授权就合并PR #186。
- 禁止修改正式足球模型、正式数据、config或唯一CURRENT。
- 禁止启动足球研究、训练、评分、调参、读取新盲标签或访问付费Provider。
- 禁止把本工程护栏写成正式模型能力提升。
- 禁止因为旧R45A状态仍存在而切回旧研究下一步。

## 上一个已完成研究状态（仅背景，不得覆盖当前IN_PROGRESS）

- previous_phase: R45A_RETROSPECTIVE_PLAYER_CAPABILITY_300_COMPLETE
- previous_result: 原始绝对球员能力汇总为稳定负增量，不晋级，formal_weight=0。
- previous_research_head: 39450499a501bad1fb5671812f89eb18379b99bf
- previous_next_step: 暂停；仅在当前IN_PROGRESS任务关闭后才允许重新考虑。

## 正式规则状态

- current_rule_required: true
- current_rule_count: 1
- current_rule_identity: 足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx
- current_rule_status: VERIFIED_FULL_READ_AND_APPLIED
- formal_weight_change: 0

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: recsrdfjCL8o1ddOuE
- airtable_state_version: 15
- airtable_sync_status: ACTIONS_PASS_PR_DRAFT_AWAITING_USER_MERGE_AUTHORIZATION
