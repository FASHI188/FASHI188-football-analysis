# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 16
- updated_at_utc: 2026-08-13T05:25:00Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_GITHUB_LIVE_STATE
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态（最高优先级）

- status: IN_PROGRESS
- task: GitHub应用与Actions质量安全整改
- completed_steps: 前三个GitHub App已由用户安装；仓库workflow/治理入口已盘点；无Secret质量安全guard已实现；Draft PR #186已建立并通过上一exact-HEAD Actions；插件/Actions职责已完成实时复核；Renovate已由PR #185确认接入生效
- current_step: 修正PR #186的Action运行时版本与主分支门禁，再治理Renovate #185并逐项验证第三方App真实check
- next_action: 1) PR #186将actions/checkout与actions/setup-python升级到v7并重跑exact-HEAD；2) 重新验收#186；3) 整改Renovate #185为禁止automerge、限制并发/频率、major人工批准；4) 逐项核验第三方App真实check；5) 建立main required status checks/branch protection或ruleset后才允许称为正式合并门禁
- source_thread: 足球研究进展
- started_at: 2026-08-13T13:02:00+08:00
- exact_head: 7b213f3b623d74000165304b237d5190b65c8e3c
- branch: chore/github-actions-quality-security-r1
- pull_request: #186
- pull_request_state: Draft/Open/未合并
- related_pull_request: #185 Renovate onboarding / Open / 未合并
- acceptance: PLUGIN_ACTIONS_AUDIT_COMPLETE_REMEDIATION_REQUIRED
- formal_model_change: 0
- formal_data_change: 0
- config_change: 0
- CURRENT_change: 0

## 实时插件与Actions复核

- Renovate: 仓库证据确认接入；renovate[bot]已创建PR #185。
- Renovate当前配置: `renovate.json`仅`extends: ["config:recommended"]`，不得原样合并。
- Renovate当前计划: Python 3.14、actions/checkout v7、actions/setup-python v7。
- PR #186上一exact-HEAD: 7b213f3b623d74000165304b237d5190b65c8e3c。
- PR #186上一质量安全run: 31669557867 / job 94351192037 / success。
- PR #186上一Repository Integrity run: 31669557872 / success。
- PR #186当前警告: actions/checkout@v4与actions/setup-python@v5触发Node.js 20 deprecated warning，因此必须先升级再重验。
- exact-HEAD可见check: Changed-file quality and security guard、repository-integrity；两者均来自GitHub Actions。
- 第三方App: 用户确认安装不等于仓库生效；必须分别记录“用户确认安装 / 仓库证据接入 / 实际Check验证通过”。
- main实时状态: protected=false；required status checks enforcement=off。
- 裁决: 当前Actions已经能运行和发现问题，但尚未形成main强制合并门禁。

## 新对话启动硬门

- 新对话启动时必须先读取本文件和Airtable《足球项目接续》当前状态。
- 只要存在 `status=IN_PROGRESS`，必须优先续做该任务。
- `IN_PROGRESS` 未关闭前，禁止按旧研究“唯一下一步”另开研究路线。
- 用户下达“开始处理”后，必须先写入进行中任务到 Airtable 当前状态、维护日志和 PROJECT_CURRENT，再允许开始实际工作。
- 进行中任务必须至少记录：task、completed_steps、current_step、next_action、source_thread、started_at、exact_head。
- exact_head 未经实时核验时必须写 `UNKNOWN`，不得用旧聊天记忆冒充实时HEAD。
- GitHub App状态禁止只写“已安装”；必须区分用户确认、仓库接入证据和实际check。

## 本任务允许事项

- 继续整改PR #186 Action版本并重跑exact-HEAD。
- 只读核验第三方App实际check与仓库接入证据。
- 整改Renovate #185配置，但未获用户明确授权不得合并。
- 核验并设计main required checks/branch protection/ruleset。

## 本任务禁止事项

- 禁止直接合并PR #186或#185。
- 禁止把“用户确认安装”写成“已实际保护仓库”。
- 禁止在main无required checks时宣称Actions已形成强制门禁。
- 禁止修改正式足球模型、正式数据、config或唯一CURRENT。
- 禁止启动足球研究、训练、评分、调参、读取新盲标签或访问付费Provider。
- 禁止切回旧R45A研究路线。

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
- state_log_record_id: recnZp5wbhIMSwer4
- airtable_state_version: 16
- airtable_sync_status: PLUGIN_ACTIONS_AUDIT_LOGGED_REMEDIATION_PENDING
