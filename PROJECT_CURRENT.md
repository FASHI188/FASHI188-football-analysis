# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 33
- updated_at_utc: 2026-08-13T07:34:00Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_LEGACY_FOOTBALL2_DEPRECATED
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态（最高优先级）

- status: WAITING
- task: R45B零标签PIT角色可用性/替代差/对位交互研究：数据就绪门已完成，效果验证停止
- branch: research/r45b-pit-role-availability-zero-label
- exact_head: b75678b7ec0eda7438f43339a58a0b94da32392c
- latest_action_run: 31674772403
- latest_action_job: 94366845682
- latest_action_execution: completed/success
- scientific_gate: STOP_FORWARD_CAPTURE_NOT_READY
- current_step: 新对话接续持久层已完成；旧足球2.txt已降为历史废弃入口；R45B恢复WAITING
- next_action: 等待用户选择R45B继续免费公开源前向零标签采集，或切换下一研究线
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
- LAST_HANDOFF 必须在每次实质进展、方向变化、阻塞和回答收尾滚动覆盖更新。

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

## 最近工程状态

- App联调 PR #190 已关闭未合并。
- GitGuardian、SonarQube Cloud、Codecov、DeepSource、Renovate 均已接收仓库事件。
- Renovate 已有 PR #187 证明真实运行。
- SimpleBackups 已由用户取消。

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: recZBJpHubEdgQUwC
- airtable_state_version: 33
- airtable_sync_status: BOOTSTRAP_ACTIVE_LEGACY_FOOTBALL2_DEPRECATED
