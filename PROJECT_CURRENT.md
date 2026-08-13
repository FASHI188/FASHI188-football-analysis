# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 14
- updated_at_utc: 2026-08-13T05:02:00Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_USER_DIRECTIVE_IN_PROGRESS_TASK

## 当前状态（最高优先级）

- status: IN_PROGRESS
- task: GitHub应用与Actions质量安全整改
- completed_steps: 前三个GitHub App已由用户安装
- current_step: 实现无需账号授权的Actions检查
- next_action: 盘点仓库现有workflow并建立独立整改PR
- source_thread: 足球研究进展
- started_at: 2026-08-13T13:02:00+08:00
- exact_head: UNKNOWN
- branch: TO_BE_CREATED
- pull_request: TO_BE_CREATED
- formal_model_change: 0
- formal_data_change: 0
- config_change: 0
- CURRENT_change: 0

## 新对话启动硬门

- 新对话启动时必须先读取本文件和Airtable《足球项目接续》当前状态。
- 只要存在 `status=IN_PROGRESS`，必须优先续做该任务。
- `IN_PROGRESS` 未关闭前，禁止按旧研究“唯一下一步”另开研究路线。
- 用户下达“开始处理”后，必须先写入进行中任务到 Airtable 当前状态、维护日志和 PROJECT_CURRENT，再允许开始实际工作。
- 进行中任务必须至少记录：task、completed_steps、current_step、next_action、source_thread、started_at、exact_head。
- exact_head 未经实时核验时必须写 `UNKNOWN`，不得用旧聊天记忆冒充实时HEAD。

## 本任务允许事项

- 盘点仓库现有 GitHub Actions workflow、Python测试依赖和安全/质量检查入口。
- 优先复用现有仓库测试与配置，建立无需额外账号、无需Secret、无需付费服务的独立质量/安全 workflow。
- 建立独立整改分支和 Draft PR；在PR内运行和验证质量/安全检查。
- 使用已安装的 GitHub App / ChatGPT GitHub 连接器完成仓库读取、分支、文件、PR和Actions相关工作。

## 本任务禁止事项

- 禁止修改正式足球模型、正式数据、config或唯一CURRENT。
- 禁止启动足球研究、训练、评分、调参、读取新盲标签或访问付费Provider。
- 禁止把尚未执行或尚未通过的Actions检查写成“已完成”。
- 禁止因为旧R45A状态仍存在而切回旧研究下一步。

## 上一个已完成研究状态（仅背景，不得覆盖当前IN_PROGRESS）

- previous_phase: R45A_RETROSPECTIVE_PLAYER_CAPABILITY_300_COMPLETE
- previous_result: 原始绝对球员能力汇总为稳定负增量，不晋级，formal_weight=0。
- previous_research_head: 39450499a501bad1fb5671812f89eb18379b99bf
- previous_execution_run: 31664611305
- previous_execution_job: 94336376824
- previous_execution_artifact: 9167483574
- previous_execution_verdict: R45A_RETROSPECTIVE_COMPLETE_RAW_PLAYER_LAYER_NEGATIVE_INCREMENT
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
- state_log_record_id: recEuDi7hAI5yb54l
- airtable_state_version: 14
- airtable_sync_status: IN_PROGRESS_TASK_REGISTERED
