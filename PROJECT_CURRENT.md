# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 25
- updated_at_utc: 2026-08-13T06:26:00Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_EXACT_HEAD_ACTIONS_ZERO_LABEL
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态（最高优先级）

- status: IN_PROGRESS
- task: R45B零标签PIT角色可用性/替代差/对位交互研究
- branch: research/r45b-pit-role-availability-zero-label
- exact_head: 016b7b01180fb6f584757ccd4c3798c44e7c4336
- action_run: 31673774317
- action_job: 94363757773
- action_execution: completed/success
- scientific_gate: STOP_R45B_PIT_INPUTS_INCOMPLETE
- current_step: 继续零标签深挖仓库，确认首轮未识别的细角色、替代差、对位交互、PIT xG/过程能力和任务效用资产是否存在
- next_action: 若现有仓库仍无可证明赛前available_at的五个缺失轴，则形成明确forward-capture输入契约并停止效果验证；不得训练、评分或读取新结果标签
- source_thread: 足球研究进展
- started_at: 2026-08-13T14:19:00+08:00
- formal_model_change: 0
- formal_data_change: 0
- config_change: 0
- CURRENT_change: 0

## R45B零标签门实际结果

- stage: ZERO_LABEL_PIT_INPUT_GATE
- labels_read: 0
- training_runs: 0
- scoring_runs: 0
- tuning_runs: 0
- provider_requests: 0
- paid_provider_requests: 0
- formal_weight: 0
- independent_oos_authorized: false
- automatic_promotion: false

### 已通过输入轴

- pit_availability: true
- pit_predicted_xi: true
- pit_synchronized_market: true

### 当前缺失输入轴

- pit_detailed_roles: false
- pit_replacement_gap_inputs: false
- pit_matchup_interaction_inputs: false
- pit_xg_process_capability: false
- pit_task_utility: false

### 覆盖证据

- match_context_live: 39份全部为赛前观察
- match_context_live_availability: 32/39
- match_context_live_predicted_xi_both: 26/39
- synchronized_market_candidates: 29/29具备完整1X2+AH+OU、赛前时间戳和同窗surface时间
- fpl_context_v519: 3040行；无row-level赛前available_at，RETROSPECTIVE_REFERENCE_ONLY
- lineup_archive: 23个JSONL；Transfermarkt 16、StatsBomb Open 7；未证明原始赛前available_at前仅RETROSPECTIVE_REFERENCE_ONLY
- team_configuration_weekly_latest_records: 334；首轮严格R45B detector未识别出可满足缺失核心轴的证据

## R45A已验证结论

- phase: R45A_RETROSPECTIVE_PLAYER_CAPABILITY_300_COMPLETE
- branch: research/r45a-player-capability-300-retro
- head: 39450499a501bad1fb5671812f89eb18379b99bf
- result: M4球员绝对能力层相对M3 LogLoss恶化 +0.011655
- bootstrap_90ci: [+0.002937,+0.020333]
- ruling: 原始绝对球员战斗力汇总不能作为无条件正式增量；formal_weight=0
- hard_stop: 禁止继续在原299场事后调绝对评分或组合方式

## PIT硬边界

- 可证明available_at优先使用source-native且可验证发布时间；否则使用collector_first_observed_at
- dataset build time、match date、retrospective page updated_at、synthetic known_at、赛后抓取回填均不得替代available_at
- 历史静态首发/伤停若无原始赛前时间，只能作回顾参考
- 同步市场只有在fixture/freeze身份独立绑定后才可进入后续研究

## 插件与工程治理收口

- GitGuardian: USER_CONFIRMED_INSTALLED
- SonarQube Cloud: USER_CONFIRMED_INSTALLED；repository config merged
- Renovate: VERIFIED_RUNNING
- SimpleBackups for GitHub: CANCELLED_BY_USER
- PR #186/#185/#189: merged

## 新对话接续硬门

- 新对话先读取本文件和Airtable《足球项目接续》当前状态。
- 只要 `status=IN_PROGRESS`，必须优先继续R45B缺失PIT输入轴的零标签深挖。
- 当前零标签门是科学STOP，不是Actions失败。
- 未补齐五个缺失轴前，禁止授权独立OOS训练/评分。
- 禁止切回R45A的299场继续调球员绝对评分。
- SimpleBackups已取消，不得重新作为阻塞项。

## 研究禁止事项

- 禁止修改唯一正式CURRENT。
- 禁止修改正式模型、正式数据或正式config。
- 禁止读取未授权盲测标签。
- 禁止访问付费Provider或新增付费API请求。
- 禁止赛后信息倒灌赛前available_at。
- 禁止人工补造伤停、首发时间戳、角色、xG、任务效用或市场同步证据。

## 正式规则状态

- current_rule_count: 1
- current_rule_identity: 足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx
- current_rule_status: VERIFIED_FULL_READ_AND_APPLIED
- formal_weight_change: 0

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: rec9HGQZgyANxtVLW
- airtable_state_version: 25
- airtable_sync_status: R45B_ZERO_LABEL_GATE_STOP_INPUTS_INCOMPLETE_CONTINUE_INVENTORY
