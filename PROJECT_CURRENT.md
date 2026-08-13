# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 34
- updated_at_utc: 2026-08-13T08:21:10Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_R45B_ZERO_LABEL_FORWARD_CAPTURE
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态（最高优先级）

- status: WAITING
- task: R45B 免费公开源前向零标签采集：首轮候选来源审计已完成，等待合同级可核验证据
- branch: research/r45b-pit-role-availability-zero-label
- exact_head: c1aa5fc54063562d32103354193b309f9dd2e7be
- latest_action_run: 31674772403
- latest_action_job: 94366845682
- latest_action_execution: completed/success
- scientific_gate: STOP_FORWARD_CAPTURE_NOT_READY
- current_step: 完成 Alaves vs Getafe 的首轮前向来源审计；未制造 validator evidence
- next_action: 继续免费公开源前向零标签采集，只接受合同级可核验证据；若角色/替代/task/process 仍不足则继续 fail-closed
- formal_model_change: 0
- formal_data_change: 0
- config_change: 0
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
- matchup_interaction: MISSING
- task_utility: MISSING
- process_capability: PARTIAL_NOT_PASS
- forward_capture_record_count: 0
- independent_oos_authorized: false
- source_audit_fixture: ESP_LaLiga__Deportivo_Alaves__Getafe_CF__20260815T173000Z
- source_audit_artifact: football-data/research/r45b_forward_capture_source_audit_20260813.json
- source_audit_ruling: NO_CONTRACT_VALID_VALIDATOR_RECORD_CREATED

## R45B本轮来源裁决

- official LALIGA / Getafe CF: 可用于比赛身份与开球时点核验；不自动构成角色/替代/task evidence。
- AS probable XI: 只支持可能首发姓名与宽泛分组；详细战术槽位不可人工补全，因此拒绝进入 expected_xi_roles evidence。
- Cadena SER: 信息内容可作发现参考，但页面法律声明明确反对机械读取/AI 使用；本项目不把其正文灌入研究数据集。
- Comuniate / Starting11: 当前抓取表示不足以提供合同级、两队同 freeze 的详细战术角色首发；拒绝进入 validator evidence。
- ESP_LaLiga process: repository readiness 只支持 `SHOTS_SOT_PROXY` shadow readiness；true_xg_ready=false；没有真实 target-fixture feature payload 时不得创建 process evidence。
- task_utility: 未找到既有固定 executable extractor；禁止手工叙事打分。

## 最近工程状态

- R45B source-audit research commit: `c1aa5fc54063562d32103354193b309f9dd2e7be`
- source-audit 不在 `football-r45b-pit-role-zero-label.yml` 的 push path 中，因此没有新 workflow run；不得把旧 run 当成本轮重新验证。
- App联调 PR #190 已关闭未合并。
- GitGuardian、SonarQube Cloud、Codecov、DeepSource、Renovate 均已接收仓库事件。
- Renovate 已有 PR #187 证明真实运行。
- SimpleBackups 已由用户取消。

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: rec9tUtwf3voJHCzM
- airtable_state_version: 34
- airtable_sync_status: R45B_ZERO_LABEL_SOURCE_AUDIT_RECORDED_FAIL_CLOSED
