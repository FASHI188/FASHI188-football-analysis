# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 24
- updated_at_utc: 2026-08-13T06:19:00Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_NEXT_STEP_FROM_R45A_LOG
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态（最高优先级）

- status: IN_PROGRESS
- task: R45B零标签PIT角色可用性/替代差/对位交互研究
- completed_steps: SimpleBackups已由用户取消；工程治理不再阻塞；Airtable最新R45A日志已核验，确认原始绝对球员评分层显著负增量且不得继续在原299场调参
- current_step: 零标签盘点仓库现有角色/首发/伤停/xG事件/任务/同步市场资产及available_at证据，建立fail-closed PIT覆盖门
- next_action: 建立独立research分支；只读盘点与零标签脚本化覆盖审计；不读新标签、不训练、不评分；门通过后才设计独立OOS授权
- source_thread: 足球研究进展
- started_at: 2026-08-13T14:19:00+08:00
- exact_head: UNKNOWN
- previous_research_head: 39450499a501bad1fb5671812f89eb18379b99bf
- formal_model_change: 0
- formal_data_change: 0
- config_change: 0
- CURRENT_change: 0

## R45A已验证结论

- phase: R45A_RETROSPECTIVE_PLAYER_CAPABILITY_300_COMPLETE
- branch: research/r45a-player-capability-300-retro
- head: 39450499a501bad1fb5671812f89eb18379b99bf
- sample: 固定五大联赛各60场共300场；外部身份匹配299/300
- result: M4球员绝对能力层相对M3 LogLoss恶化 +0.011655
- bootstrap_90ci: [+0.002937,+0.020333]
- ruling: 原始绝对球员战斗力汇总不能作为无条件正式增量；formal_weight=0
- hard_stop: 禁止继续在本299场事后调绝对评分或组合方式

## R45B预注册边界

- stage_type: ZERO_LABEL_PIT_INPUT_GATE
- primary_inputs:
  - 角色可用性
  - 替代差
  - 对位交互
  - 伤停
  - 预计/确认首发 available_at
  - xG/事件过程能力
  - 比赛任务效用
  - 同步市场
- first_gate: 输入存在性、时间戳、比赛前可用性、覆盖率、身份一致性、重复/冲突检查
- labels: 禁止读取新结果标签
- training: 0
- scoring: 0
- tuning: 0
- paid_provider: 0
- formal_weight: 0
- fail_closed: 缺少available_at或无法证明赛前可用时必须STOP，不人工补造

## 插件与工程治理收口

- GitGuardian: USER_CONFIRMED_INSTALLED
- SonarQube Cloud: USER_CONFIRMED_INSTALLED；repository config merged
- Renovate: VERIFIED_RUNNING
- SimpleBackups for GitHub: CANCELLED_BY_USER
- PR #186/#185/#189: merged

## 新对话接续硬门

- 新对话先读取本文件和Airtable《足球项目接续》当前状态。
- 只要 `status=IN_PROGRESS`，必须优先继续R45B零标签PIT输入门。
- 禁止切回R45A的299场继续调球员绝对评分。
- 只有零标签PIT门通过后，才允许另行预注册并授权独立OOS效果验证。
- SimpleBackups已取消，不得重新作为阻塞项。

## 研究禁止事项

- 禁止修改唯一正式CURRENT。
- 禁止修改正式模型、正式数据或正式config。
- 禁止读取未授权盲测标签。
- 禁止访问付费Provider或新增付费API请求。
- 禁止赛后信息倒灌赛前available_at。
- 禁止人工补造伤停、首发时间戳、xG、任务效用或市场同步证据。

## 正式规则状态

- current_rule_count: 1
- current_rule_identity: 足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx
- current_rule_status: VERIFIED_FULL_READ_AND_APPLIED
- formal_weight_change: 0

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: rec8nJC9Vzu3b2yox
- airtable_state_version: 24
- airtable_sync_status: R45B_PIT_ROLE_AVAILABILITY_ZERO_LABEL_STARTED
