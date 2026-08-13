# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 36
- updated_at_utc: 2026-08-13T09:24:21Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_R45B_ZERO_LABEL_READINESS_OOS_PREREG_AND_SAMPLE_1_OF_300
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态（最高优先级）

- status: WAITING
- task: R45B 已完成零标签 PIT 数据完整性门与独立 OOS 预注册；当前按冻结规则前向积累 300 场完全合格零标签样本
- branch: research/r45b-pit-role-availability-zero-label
- exact_head: 028cbced2db6c8b7046114e36e1f09184e6c9a3d
- latest_action_run: 31686297563
- latest_action_job: 94403038637
- latest_action_execution: completed/success
- data_readiness_gate: PASS
- oos_preregistration_gate: PASS
- scientific_gate: OOS_SAMPLE_COVERAGE_CLOSED_1_OF_300
- current_step: 第 1 个前向 OOS 样本候选 Alaves-Getafe 已通过 sample manifest validator；fully_eligible=1，remaining=299，coverage_gate_pass=false
- next_action: 继续按已冻结 R45B OOS prereg 前向采集剩余 299 个完全合格零标签 fixture bundle；未达到覆盖门前不得冻结 sample、不得读取标签、不得训练或评分
- formal_model_change: 0
- formal_data_change: 0
- research_evidence_change: forward records 7；新增研究 1X2 baseline 1；OOS sample manifest 1/300
- config_change: 0（正式配置）；研究 extractor / validator / workflow / prereg 有更新
- CURRENT_change: 0

## 新对话永久接续治理

- 当前启动器：`足球项目_START_HERE_新对话永久接续启动器.txt`
- `LAST_HANDOFF.md`：上一条对话末端接力层。
- `PROJECT_CURRENT.md` + Airtable：状态验证层。
- 新聊天固定读取顺序：LAST_HANDOFF → PROJECT_CURRENT → Airtable当前状态 → 绑定维护日志 → 最新维护日志 → 唯一CURRENT。
- Airtable START HERE 为固定 bootstrap，不保存动态 Rxx / HEAD / run。
- LAST_HANDOFF 必须在每次实质进展、用户改变方向、阻塞和回答收尾滚动覆盖更新。

## 正式规则与旧入口

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0
- `足球2.txt`: DEPRECATED_LEGACY_ENTRY，execution_weight=0；不得覆盖唯一CURRENT、LAST_HANDOFF、PROJECT_CURRENT或Airtable。

## R45B 零标签数据完整性门

- target_match_labels_read: 0
- training_runs: 0
- scoring_runs: 0
- tuning_runs: 0
- provider_requests: 0
- paid_provider_requests: 0
- formal_weight: 0
- forward_capture_validator_status: READY_FOR_SEPARATE_OOS_PREREGISTRATION
- forward_capture_record_count: 7
- valid_record_count: 7
- invalid_record_count: 0
- expected_xi_roles: PASS（2 records；两队同一 freeze）
- availability_and_replacement: PASS（1 record）
- process_capability: PASS（2 records；`SHOTS_SOT_PROXY`，不得称 true xG）
- task_utility: PASS（2 records）
- matchup_interaction_gate: PASS（依赖同 freeze role-XI 配对；不代表已人工打 matchup 分）
- blocking_axes: []
- same_freeze_both_team_role_xi_fixture_count: 1
- readiness_hard_assert_run_job: 31685408164 / 94400180975
- readiness_ruling: DATA_COMPLETENESS_PASS_ONLY_NOT_PREDICTIVE_EFFECTIVENESS

## 第一场前向 PIT bundle

- fixture_key: ESP_LaLiga__Deportivo_Alaves__Getafe_CF__20260815T173000Z
- kickoff_at_utc: 2026-08-15T17:30:00Z
- role_xi_same_freeze_at_utc: 2026-08-13T09:07:14Z
- expected formations from source-backed current guides: Alaves 4-4-2；Getafe 5-3-2
- role policy: 球员仅使用来源明确给出的标准 position slot；不得从阵型顺序人工推断 LCB/RCB/RWB/LWB/LM/RM 等本场部署
- availability evidence: Lucas Boyé 赛前伤情 + Mariano / Aitor Mañas 明确替代候选链
- process: 两队各自最近 10 场严格先验 HS/HST/AS/AST；只输出 SHOTS_SOT_PROXY；结果/进球列禁用
- task: 两队严格赛前 schedule_fatigue 结构化事实；不人工赋予 fatigue/motivation 权重

## 独立 OOS 预注册

- prereg_path: `football-data/research/r45b_independent_oos_preregistration.json`
- prereg_schema: R45B-INDEPENDENT-OOS-PREREG-R1
- prereg_sha256: b91ff8547151aacebc94aa91bb82f4425de7974b8c09b943c214f63d8065e159
- prereg_validation_status: PASS_PREREGISTRATION_ZERO_LABEL_WAITING_SAMPLE_MANIFEST
- prereg_validation_run_job: 31686029804 / 94402185269
- mandatory_zero_label_fields: 13 / 13
- candidate_count: 1
- candidate_id: R45B_DRAW_MASS_LOGIT_OFFSET_R1
- baseline: 同 bundle-freeze、单一 bookmaker、完整 90分钟 H/D/A 价格 → 固定 no-vig 概率；无合格基线则整场排除
- primary_metric: paired multiclass H/D/A LogLoss delta，challenger-baseline，负值更好
- draw_required_metric: binary Draw-vs-Not-Draw LogLoss delta 必须同步改善
- sample_gate: >=300 fully eligible prospective fixtures
- split: expanding chronological OOS；约 120 初始训练块 + 3 个顺序 test folds；同 UTC 日期整块防泄漏
- uncertainty_gate: paired calendar-date-block bootstrap 2000 次；90% 区间上界 < 0
- no_search: 禁止结果后 candidate search、threshold tuning、feature selection、retune reuse
- independent_oos_authorized: false
- generic_continue_is_authorization: false

## 300 场零标签 OOS 样本 manifest

- manifest_path: `football-data/research/r45b_oos_sample_manifest.json`
- manifest_status: OPEN_ZERO_LABEL_ACCUMULATING
- registered_fixture_count: 1
- fully_eligible_fixture_count: 1
- minimum_required: 300
- remaining_to_minimum: 299
- competition_count: 1
- coverage_gate_pass: false
- sample_frozen: false
- authorization_ready: false
- first_fixture: ESP_LaLiga__Deportivo_Alaves__Getafe_CF__20260815T173000Z
- first_bundle_freeze_at_utc: 2026-08-13T09:20:03Z
- first_sample_validation_run_job: 31686297563 / 94403038637
- first_sample_validation: PASS_ACCUMULATING_ZERO_LABEL_NOT_AUTHORIZABLE
- target_label_status: UNREAD_FORBIDDEN

## 第一场研究 1X2 基线裁决

- bookmaker: FanDuel
- observed research prices: Alaves +130 / Draw +190 / Getafe +240
- decimal: 2.30 / 2.90 / 3.40
- no_vig: H 0.4049281314168378 / D 0.3211498973305955 / A 0.27392197125256673
- collector_first_observed_at_utc: 2026-08-13T09:20:03Z
- native_quote_timestamp: unavailable
- baseline_status: RESEARCH_ONLY_PROSPECTIVE_QUERY_TIME
- formal_market_snapshot: false
- source_taxonomy_warning: 聚合页标题赛事分类与官方 LaLiga 身份不一致；只因精确 fixture 与单一 bookmaker 三项报价可核验而保留作研究基线；不得升级为正式市场快照

## 当前硬禁区

- 未达到 >=300 fully eligible + 赛事覆盖门前，不得冻结 OOS sample identity。
- sample identity 未冻结并经单独明确授权前，不得读取 target labels、训练、评分或调参。
- “继续”不构成 independent OOS authorization。
- 不得结果后补阵容/伤停/市场来凑合格样本。
- 不得降低 300 场门或用单场命中、accuracy、AUC、ROI 代替预注册 proper-score gate。
- 不得调用付费 Provider；不得改正式模型、formal_weight、正式 config 或 CURRENT。

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: recAqUN9Mgn7Guztm
- airtable_state_version: 36
- airtable_sync_status: R45B_READINESS_AND_OOS_PREREG_PASS_SAMPLE_ACCUMULATING_1_OF_300
