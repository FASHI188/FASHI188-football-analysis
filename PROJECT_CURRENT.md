# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 35
- updated_at_utc: 2026-08-13T08:45:19Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_R45B_TASK_UTILITY_FORWARD_EVIDENCE
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态（最高优先级）

- status: WAITING
- task: R45B 免费公开源前向零标签采集：task_utility 已形成可执行主链并验证通过，继续补剩余合同级证据轴
- branch: research/r45b-pit-role-availability-zero-label
- exact_head: 241c1f12b7f163d80df528e86ae90b1662bfa00a
- latest_action_run: 31683350688
- latest_action_job: 94393642748
- latest_action_execution: completed/success
- scientific_gate: STOP_FORWARD_CAPTURE_NOT_READY
- current_step: task_utility 已有 2 条 validator-valid prospective/query-time 记录；invalid_record_count=0；完整 readiness 仍未通过
- next_action: 继续免费公开源前向零标签采集 expected_xi_roles、availability_and_replacement、process_capability；matchup_interaction 等待角色首发轴后再运行
- formal_model_change: 0
- formal_data_change: 0
- research_evidence_change: +2 task_utility forward records
- config_change: 0（正式配置）；研究 workflow/extractor 有更新
- CURRENT_change: 0

## 新对话永久接续治理

- 当前启动器：`足球项目_START_HERE_新对话永久接续启动器.txt`
- `LAST_HANDOFF.md`：上一条对话末端接力层。
- `PROJECT_CURRENT.md` + Airtable：状态验证层。
- 新聊天固定读取顺序：LAST_HANDOFF → PROJECT_CURRENT → Airtable当前状态 → 绑定维护日志 → 最新维护日志。
- Airtable START HERE 为固定bootstrap，不保存动态Rxx/PR/HEAD。
- LAST_HANDOFF 必须在每次实质进展、用户改变方向、阻塞和回答收尾滚动覆盖更新。

## 旧足球2.txt裁决

- file_identity: `足球2.txt`
- verified_content: 仅包含历史 V3.5.1 Word 版链接。
- visibility: 用户当前项目可见文件列表中未找到；文件检索仍能定位旧上传记录。
- status: DEPRECATED_LEGACY_ENTRY
- execution_weight: 0
- deletion_requirement: NOT_REQUIRED
- handling: 若未来可见可删除；不可见直接忽略，不得阻塞新对话接续。
- prohibition: 不得用该文件覆盖唯一CURRENT、LAST_HANDOFF、PROJECT_CURRENT或Airtable当前状态。

## R45B科学暂停点

- target_match_labels_read: 0
- training_runs: 0
- scoring_runs: 0
- tuning_runs: 0
- provider_requests: 0
- paid_provider_requests: 0
- formal_weight: 0
- detailed_roles: MISSING
- replacement_gap: MISSING
- matchup_interaction: MISSING_WAITING_ROLE_AXIS
- task_utility: PASS
- task_utility_valid_record_count: 2
- task_utility_invalid_record_count: 0
- task_utility_extractor: R45B-TASK-STATE-EXTRACTOR-R1
- process_capability: PARTIAL_NOT_PASS
- forward_capture_record_count: 2
- independent_oos_authorized: false
- source_audit_fixture: ESP_LaLiga__Deportivo_Alaves__Getafe_CF__20260815T173000Z
- task_evidence_freeze_at_utc: 2026-08-13T08:39:48Z
- task_evidence_scope: schedule_fatigue for Deportivo Alaves and Getafe CF
- task_evidence_ruling: VALIDATOR_ACCEPTED_AND_ASSERTED

## R45B本轮来源裁决

- official LALIGA / Getafe CF: 可用于比赛身份与开球时点核验；不自动构成角色/替代 evidence。
- official Racing / Tottenham / Getafe / AS Monaco pre-season schedules: 允许作为严格赛前的 schedule_fatigue source-backed facts；已生成两条 task_utility 记录。
- FotMob 当前可提供部分伤停/停赛身份，但缺合同要求的可靠 replacement candidate + depth/selection 证据，因此暂不进入 availability_and_replacement。
- AS probable XI / Comuniate: 可作发现参考；目前仍不足以形成“两队同 freeze、11 人明确 role/position slot + formation/shape”的 expected_xi_roles 合同证据。
- Cadena SER: 页面法律声明反对机械读取/AI 使用；不把其正文灌入研究数据集。
- ESP_LaLiga process: repository readiness 只支持 `SHOTS_SOT_PROXY` shadow readiness；true_xg_ready=false；没有真实 target-fixture feature payload 时不得创建 process evidence。
- task_utility: 已补固定可执行 extractor；只允许固定结构化事实，不允许人工 motivation/fatigue 权重。

## 最近工程状态

- R45B current research HEAD: `241c1f12b7f163d80df528e86ae90b1662bfa00a`
- 新增 `football-data/research/r45b_task_state_extractor.py`，固定版本 `R45B-TASK-STATE-EXTRACTOR-R1`。
- workflow 已加入 task extractor self-test 与 validator-valid task evidence 硬断言。
- latest Actions: run `31683350688` / job `94393642748`，completed/success。
- 关键断言：至少 2 条 valid `task_utility`、`axis_ready.task_utility=true`、`invalid_record_count=0`；该步骤 success。
- OOS guard 继续 success，但其含义是“未自动授权 OOS”，不是效果或科学 PASS。
- App联调 PR #190 已关闭未合并；不影响本轮 R45B 研究。
- 正式模型、正式数据、正式配置、CURRENT 均未改动。

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: recK3US3485L1c2FD
- airtable_state_version: 35
- airtable_sync_status: R45B_TASK_UTILITY_FORWARD_AXIS_VALIDATED_REMAINING_AXES_FAIL_CLOSED
