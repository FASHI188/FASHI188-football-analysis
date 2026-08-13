# 足球项目最后对话接力点

> 本文件只保存“上一条对话末端如何继续”的最新接力信息。它不是正式规则，不替代 `PROJECT_CURRENT.md` 或 Airtable 当前状态。每次有实质进展、用户改变方向、发生阻塞或准备结束当前回答时，都必须覆盖更新本文件。

## 接力身份

- project_id: football-project
- handoff_version: 7
- state_version: 35
- updated_at_utc: 2026-08-13T08:45:19Z
- status: WAITING

## 用户最后明确目标

- 用户本轮明确说“继续”。
- 已按唯一 CURRENT V5.2.0 完成启动验收，并从 state34 的唯一下一步继续 R45B 免费公开源、前向、零标签采集。
- 不得擅自切换到效果验证、训练、评分、调参、正式权重或 CURRENT 变更。

## 本轮已经完成

1. 重新核验 `LAST_HANDOFF.md`、`PROJECT_CURRENT.md`、Airtable 当前状态/绑定日志与唯一 CURRENT，state34 启动一致性通过。
2. 实时核验 main 与旧 R45B Actions；确认旧绿灯只是工程执行成功，不能当数据 readiness 或科学 PASS。
3. 读取 R45B forward-capture contract、task-state preregistration 与 validator，确认 `task_utility` 缺口的根因是“已有固定规则但没有可执行 extractor”。
4. 新增 `football-data/research/r45b_task_state_extractor.py`，版本固定为 `R45B-TASK-STATE-EXTRACTOR-R1`；仅输出结构化 schedule/travel/rotation/competition facts，禁止人工概率、进球、motivation/fatigue 权重。
5. workflow 加入 extractor self-test；run `31682993809 / 94392521087` completed/success。
6. 用赛前公开官方赛程事实为 Deportivo Alaves 与 Getafe CF 生成 2 条 prospective/query-time `schedule_fatigue` task_utility 证据，freeze=`2026-08-13T08:39:48Z`。
7. 为避免把 validator 的 fail-closed 绿灯误读成数据通过，进一步加入硬断言：`valid task_utility >= 2`、`axis_ready.task_utility=true`、`invalid_record_count=0`。
8. exact HEAD `241c1f12b7f163d80df528e86ae90b1662bfa00a` 的 Actions run `31683350688` / job `94393642748` 全部 success，且 `Assert captured task-utility evidence validated` 单独 success。
9. 因此 R45B `task_utility` 轴可正式从 MISSING 改为 PASS；forward capture 记录数=2，invalid=0。
10. 完整科学门仍为 `STOP_FORWARD_CAPTURE_NOT_READY`：`expected_xi_roles`、`availability_and_replacement`、`process_capability` 仍缺；`matchup_interaction` 等待角色轴。
11. 本轮 target labels=0、training=0、scoring=0、tuning=0、Provider=0、paid Provider=0、formal_weight=0。
12. 已追加 Airtable state35 维护日志 `recK3US3485L1c2FD`，并准备同步 PROJECT_CURRENT / 当前状态。

## 当前项目真实停点

- task: R45B 免费公开源前向零标签采集：task_utility 已验证通过，继续补剩余合同级证据轴
- branch: `research/r45b-pit-role-availability-zero-label`
- exact_head: `241c1f12b7f163d80df528e86ae90b1662bfa00a`
- latest_action_run/job: `31683350688 / 94393642748`
- latest_action_execution: `completed/success`
- scientific_gate: `STOP_FORWARD_CAPTURE_NOT_READY`
- forward_capture_record_count: 2
- task_utility: PASS（2 valid / 0 invalid）
- detailed_roles: MISSING
- replacement_gap: MISSING
- process_capability: PARTIAL_NOT_PASS
- matchup_interaction: MISSING_WAITING_ROLE_AXIS
- independent_oos_authorized: false
- target labels / 训练 / 评分 / 调参 / Provider / 付费 Provider / formal_weight 全部 0。

## 已完成、禁止重复

- task_utility 不再是“无 extractor”问题；固定 extractor 已实现并经 Actions self-test。
- 不要再手工给 motivation、fatigue、travel 打权重；只能按固定 extractor 输出 source-backed 结构化事实。
- 不得再次把姓名列表或阵型习惯人工变成 target-fixture 详细战术角色。
- 不得把伤停名单直接等同于 replacement gap；必须有同 freeze 的角色、可靠替代候选和选择/深度证据。
- 不得把 `SHOTS_SOT_PROXY` 改称 true xG。
- 不得为了让 validator 变绿而创建只有 schema 元数据、没有真实 feature payload 的 process record。
- 不得读取目标赛果标签、训练、评分、调参、调用付费 Provider 或改变 formal_weight。
- 旧 `足球2.txt` 继续 `DEPRECATED_LEGACY_ENTRY`、execution_weight=0。

## 唯一下一步

- 继续 R45B 免费公开源、前向、零标签采集剩余阻塞轴。
- `expected_xi_roles`：必须两队同一 freeze 下各 11 人有明确 role/position slot，并有 formation/shape；仅姓名或需人工推断则拒绝。
- `availability_and_replacement`：必须同 freeze unavailable/doubtful 身份、角色、可靠 replacement candidate 与 selection/depth evidence。
- `process_capability`：必须严格先验的真实 feature payload；LaLiga 当前只能标 `SHOTS_SOT_PROXY`，true xG 不得虚构。
- `matchup_interaction`：等角色首发轴合法后，按已预注册 interaction 规则运行，不手工打分。
- 即使完整数据 readiness 后续 PASS，也必须另行预注册并获得明确 independent OOS authorization，才允许读取目标标签/训练/评分。

## 权威优先级

用户当前明确指令 > 唯一正式 CURRENT（正式规则事项） > LAST_HANDOFF（对话末端） > PROJECT_CURRENT + Airtable（状态验证） > 历史聊天/旧文件/记忆。
