# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 27
- updated_at_utc: 2026-08-13T06:54:00Z
- updated_by: GPT-5.6 Sol
- status_source: USER_AUTHORIZED_APP_INTEGRATION_QUICK_PROBE
- project_current_sha256: TO_BE_RECORDED_IN_AIRTABLE_AFTER_WRITE

## 当前状态（最高优先级）

- status: IN_PROGRESS
- task: GitHub应用一次性快速联调测试
- branch: chore/app-integration-quick-probe
- base_main_before_probe: 771d8abe41bc2bfbeabf71708c32e18f9edae985
- current_step: 创建一次性Draft PR，观察GitGuardian、SonarQube Cloud、Renovate与GitHub Actions实际响应
- next_action: 测试结束后关闭测试PR，不合并；恢复R45B WAITING
- source_thread: 足球研究进展
- formal_model_change: 0
- formal_data_change: 0
- config_change: 0
- CURRENT_change: 0

## 快速联调约束

- 测试PR必须为Draft/Open且绝不合并。
- 测试内容只能使用明确无效/示例凭据，不得使用真实Secret。
- 允许故意触发安全扫描与静态分析以验证App是否实际工作。
- 测试完成后关闭PR；测试分支不得进入main。
- 不修改正式足球模型、正式数据、config或唯一CURRENT。

## 插件与工程治理当前记录

- GitGuardian: USER_CONFIRMED_INSTALLED；待本次实时probe验证
- SonarQube Cloud: USER_CONFIRMED_INSTALLED；repository config merged；待本次实时probe验证
- Renovate: VERIFIED_RUNNING；已真实创建PR #187
- SimpleBackups for GitHub: CANCELLED_BY_USER
- GitHub Actions quality/security guard: VERIFIED_RUNNING

## R45B暂停点（本次联调结束后恢复）

- previous_status: WAITING
- branch: research/r45b-pit-role-availability-zero-label
- exact_head: b75678b7ec0eda7438f43339a58a0b94da32392c
- latest_action_run: 31674772403
- latest_action_job: 94366845682
- latest_action_execution: completed/success
- scientific_gate: STOP_FORWARD_CAPTURE_NOT_READY
- target_match_labels_read: 0
- training_runs: 0
- scoring_runs: 0
- tuning_runs: 0
- provider_requests: 0
- paid_provider_requests: 0
- formal_weight: 0
- detailed_roles: MISSING
- replacement_gap: MISSING
- matchup_interaction: MISSING
- task_utility: MISSING
- process_capability: PARTIAL_NOT_PASS
- forward_capture_record_count: 0
- independent_oos_authorized: false

## 正式规则状态

- current_rule_count: 1
- current_rule_identity: 足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx
- current_rule_status: VERIFIED_FULL_READ_AND_APPLIED
- formal_weight_change: 0

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: recsVxcFT0wr0ygNV
- airtable_state_version: 27
- airtable_sync_status: APP_INTEGRATION_QUICK_PROBE_IN_PROGRESS
