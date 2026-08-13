# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 39
- updated_at_utc: 2026-08-13T11:12:00Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_R45B_FIXTURE_2_ZERO_LABEL_FORWARD_ACCUMULATION
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态（最高优先级）

- status: WAITING
- task: R45B 独立 OOS 零标签前向样本积累
- branch: research/r45b-pit-role-availability-zero-label
- exact_head: c5625410463436c2d2bfa10b67634d92644dfdc6
- scientific_gate: OOS_SAMPLE_COVERAGE_CLOSED_2_OF_300
- fully_eligible_fixture_count: 2
- minimum_required: 300
- remaining_to_minimum: 298
- competition_count: 1
- coverage_gate_pass: false
- sample_frozen: false
- authorization_ready: false
- independent_oos_authorized: false
- next_action: 从第3个合格 prospective fixture 开始，继续严格 PIT、zero-label bundle 积累；不得重做前2场
- formal_model_change: 0
- formal_data_change: 0
- config_change: 0（正式配置）
- CURRENT_change: 0

## File Library 启动治理

- sole_START_HERE: `足球项目_START_HERE_新对话与断线永久接续启动器_唯一版.txt`
- sole_START_HERE_file_id: `file_00000000df6c8209b954fe071f813fa0`
- cleanup_status: PASS_UNIQUE_CURRENT_START_HERE
- old_duplicate_cleanup_blocker: RESOLVED
- rule: 新对话不得再恢复 state38 的旧“等待用户删除/上传 START_HERE”阻塞。

## 刷新/连接中断精确续跑治理

- stable_protocol: `ACTIVE_CHECKPOINT.md`
- live_checkpoint_store: Airtable Base《足球项目接续》表《执行检查点》
- live_checkpoint_table_id: `tblFMldlWUzFf3b3t`
- active_checkpoint_record_id: `recIRxK7EIMjJdG4A`
- checkpoint_state_version: 39
- recovery_order: ACTIVE_CHECKPOINT协议 → Airtable执行检查点 → LAST_HANDOFF → PROJECT_CURRENT → Airtable当前状态/绑定维护日志 → 必要时唯一CURRENT
- interrupted_running_rule: RUNNING/VERIFYING 时先核验副作用是否已发生；已发生禁止重复，未发生且可安全重试才重试，不确定则 BLOCKED_CHECKPOINT_SIDE_EFFECT_AMBIGUOUS。

## 正式规则状态

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0
- CURRENT_change_this_state: 0

## R45B 零标签数据完整性门

- forward_capture_validator_status: READY_FOR_SEPARATE_OOS_PREREGISTRATION
- forward_capture_record_count: 14
- valid_record_count: 14
- invalid_record_count: 0
- expected_xi_roles: PASS（4 records；2 fixtures 均为两队同一 freeze）
- availability_and_replacement: PASS（2 records）
- process_capability: PASS（4 records；`SHOTS_SOT_PROXY`，不得称 true xG）
- task_utility: PASS（4 records；固定 extractor 真实运行，不人工赋 fatigue/motivation 权重）
- matchup_interaction_gate: PASS（依赖同 freeze role-XI 配对，不代表人工打 matchup 分）
- blocking_axes: []
- fixtures_with_both_team_role_xi_same_freeze: 2
- latest_forward_capture_run_job: 31694149181 / 94427925007
- latest_forward_capture_artifact: 9178600254
- latest_forward_capture_artifact_sha256: 30c4fab20849d52d4dc715f57f4ce959aaef5edb8db9f0a05fcc2d5acad3b849
- readiness_ruling: DATA_COMPLETENESS_PASS_ONLY_NOT_PREDICTIVE_EFFECTIVENESS

## 独立 OOS 预注册

- prereg_path: `football-data/research/r45b_independent_oos_preregistration.json`
- prereg_sha256: b91ff8547151aacebc94aa91bb82f4425de7974b8c09b943c214f63d8065e159
- prereg_validation_status: PASS_PREREGISTRATION_ZERO_LABEL_WAITING_SAMPLE_MANIFEST
- candidate_id: R45B_DRAW_MASS_LOGIT_OFFSET_R1
- primary_metric: paired multiclass H/D/A LogLoss delta，challenger-baseline，负值更好
- draw_required_metric: binary Draw-vs-Not-Draw LogLoss delta 必须同步改善
- sample_gate: >=300 fully eligible prospective fixtures
- split: expanding chronological OOS；约120初始训练块 + 3个顺序test folds；同UTC日期整块防泄漏
- uncertainty_gate: paired calendar-date-block bootstrap 2000次；90%区间上界 < 0
- no_search: 禁止结果后 candidate search、threshold tuning、feature selection、retune reuse
- independent_oos_authorized: false
- generic_continue_is_authorization: false

## 300 场零标签 OOS 样本 manifest

- manifest_path: `football-data/research/r45b_oos_sample_manifest.json`
- manifest_status: OPEN_ZERO_LABEL_ACCUMULATING
- manifest_sha256_current_open_state: 5c5f5ce0859a95aa9450a467a048cdd59632493e0225f2fb41f9b1db277cafda
- registered_fixture_count: 2
- fully_eligible_fixture_count: 2
- remaining_to_minimum: 298
- coverage_gate_pass: false
- sample_frozen: false
- authorization_ready: false
- latest_sample_validation: PASS_ACCUMULATING_ZERO_LABEL_NOT_AUTHORIZABLE
- latest_sample_validation_run_job: 31694083580 / 94427719914
- latest_zero_label_guard: PASS_NO_AUTOMATIC_OOS_AUTHORIZATION

## Fixture #1：Alaves–Getafe（已完成，禁止重复）

- fixture_key: ESP_LaLiga__Deportivo_Alaves__Getafe_CF__20260815T173000Z
- kickoff_at_utc: 2026-08-15T17:30:00Z
- bundle_freeze_at_utc: 2026-08-13T09:20:03Z
- evidence_bundle: 7 records PASS
- baseline: FanDuel complete 90m 1X2，research-only query-time，no native quote timestamp
- target_label_status: UNREAD_FORBIDDEN

## Fixture #2：Sevilla–Rayo（本state新增，已完成，禁止重复）

- fixture_key: ESP_LaLiga__Sevilla_FC__Rayo_Vallecano__20260815T193000Z
- kickoff_at_utc: 2026-08-15T19:30:00Z
- bundle_freeze_at_utc: 2026-08-13T10:56:46Z
- expected_xi_pair_same_freeze_at_utc: 2026-08-13T10:56:46Z
- evidence_bundle: 7 records PASS
- Sevilla process: shots_for 12.4 / SOT_for 3.0 / shots_against 9.8 / SOT_against 3.3（最近10场严格先验）
- Rayo process: shots_for 15.1 / SOT_for 5.5 / shots_against 12.8 / SOT_against 4.8（最近10场严格先验）
- task extractor: Sevilla 距上一场174.0h；Rayo 173.5h；manual_adjustments_emitted=0
- availability chain: Marcão injured/unavailable；Sangante/Kike Salas/Castrín为来源支持的CB替代/深度链，不人工指定影响权重
- research 1X2 baseline: bet365 +125 / +210 / +220；decimal 2.25 / 3.10 / 3.20；no-vig H 0.41170367296119526 / D 0.2988171819879643 / A 0.28947914505084044
- baseline_status: RESEARCH_ONLY_PROSPECTIVE_QUERY_TIME_NO_NATIVE_QUOTE_TIMESTAMP
- formal_market_snapshot: false
- process_run_job_artifact: 31693452191 / 94425743387 / 9178330392
- task_run_job_artifact: 31693783226 / 94426786006 / 9178463002
- target_label_status: UNREAD_FORBIDDEN

## 零标签硬不变量

- target_match_labels_read: 0
- training_runs: 0
- scoring_runs: 0
- tuning_runs: 0
- provider_requests: 0
- paid_provider_requests: 0
- formal_weight: 0
- automatic_oos_authorization: false
- independent_oos_authorized: false

## 当前硬禁区

- 未达到 >=300 fully eligible + 赛事覆盖门前，不得冻结 OOS sample identity。
- sample identity 未冻结并经单独明确授权前，不得读取 target labels、训练、评分或调参。
- 普通“继续”只恢复既有 zero-label forward accumulation，不构成 independent OOS authorization。
- 不得结果后补阵容/伤停/市场来凑合格样本。
- 不得降低300场门，不得用单场命中、accuracy、AUC、ROI代替预注册 proper-score gate。
- 不得调用付费 Provider；不得改正式模型、formal_weight、正式 config 或 CURRENT。
- 总进球旁路研究按用户最新指令暂停，不得抢占当前 R45B 任务。

## Airtable 同步锚点

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: rec9iRuDB2HjBXDzh
- execution_checkpoint_record_id: recIRxK7EIMjJdG4A
- airtable_state_version: 39
- next_recovery: 从 fixture #3 开始，不重做 #1/#2。
