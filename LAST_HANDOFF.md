# 足球项目最后对话接力点

> 本文件只保存“上一条对话末端如何继续”的最新接力信息。实时原子子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》。本文件不替代 `PROJECT_CURRENT.md`、Airtable 当前状态或唯一 CURRENT。

## 接力身份

- project_id: football-project
- handoff_version: 9
- state_version: 37
- updated_at_utc: 2026-08-13T09:45:00Z
- status: WAITING

## 用户最后明确目标

- 用户反馈连接中断后刷新会重新从头执行，要求继续解决。
- 本轮只做“同一任务中途刷新/断线后的精确子步骤续跑”治理，不推进新比赛样本、不读取标签、不训练。

## 本轮已经完成

1. 已确认真实项目停点是 state36 的 R45B 独立 OOS 零标签样本积累，首场 Alaves-Getafe 已完全合格，当前 1/300、剩余 299。
2. 新增稳定协议文件 `ACTIVE_CHECKPOINT.md`。
3. Airtable Base《足球项目接续》新增表《执行检查点》，表ID=`tblFMldlWUzFf3b3t`。
4. 建立唯一激活检查点 `recIRxK7EIMjJdG4A`，当前 state_version=37；实时 checkpoint_version 与 status 只从 Airtable 读取，不在本文件硬编码。
5. 检查点当前恢复位置固定为：从第2个合格 fixture 候选开始，不重做第1场、不重跑已通过的 readiness/OOS prereg。
6. 已定义原子步骤幂等规则：开始前 RUNNING + 唯一操作ID；完成后先核验真实副作用再 COMPLETED/WAITING；若刷新时仍为 RUNNING/VERIFYING，必须先查副作用是否已发生，不能盲重试。
7. 若副作用已发生，禁止重复执行；若明确未发生且可安全重试才允许重试；若无法判断则 BLOCKED_CHECKPOINT_SIDE_EFFECT_AMBIGUOUS。
8. 检查点高频更新只递增 checkpoint_version，不递增 PROJECT_CURRENT state_version，也不为每个工具调用制造 main commit / Actions。
9. `AGENTS.md` 已升级为四层接续：ACTIVE_CHECKPOINT → LAST_HANDOFF → PROJECT_CURRENT → Airtable；并保留唯一 CURRENT 硬门。
10. `PROJECT_CURRENT.md` 已升级 state37；R45B 科学状态与正式资产均未改变。
11. state-doc-integrity Workflow 已纳入 ACTIVE_CHECKPOINT.md，会同时计算 ACTIVE/PROJECT/LAST 三份 SHA-256 并校验 PROJECT_CURRENT/LAST_HANDOFF state_version 一致。

## 当前实时执行检查点

- protocol: `ACTIVE_CHECKPOINT.md`
- Airtable table: `执行检查点`
- table_id: `tblFMldlWUzFf3b3t`
- active_record_id: `recIRxK7EIMjJdG4A`
- checkpoint_state_version: 37
- checkpoint_version: RUNTIME_AUTHORITATIVE_IN_AIRTABLE
- checkpoint_status: RUNTIME_AUTHORITATIVE_IN_AIRTABLE
- operation_id at current waiting baseline: `R45B-OOS-COLLECT-NEXT-FIXTURE`
- last_completed_substep: 第1个完全合格样本 Alaves-Getafe 已注册并通过 validator，1/300
- resume_from: 第2个合格 fixture 候选
- pending_side_effect at governance closure: NONE

## 当前项目真实停点

- task: R45B 独立 OOS 研究样本前向积累
- branch: `research/r45b-pit-role-availability-zero-label`
- exact_head: `028cbced2db6c8b7046114e36e1f09184e6c9a3d`
- data_readiness: PASS
- data_readiness_run/job: `31685408164 / 94400180975`
- oos_preregistration: PASS
- prereg_run/job: `31686029804 / 94402185269`
- oos_sample_manifest: OPEN_ZERO_LABEL_ACCUMULATING
- sample_run/job: `31686297563 / 94403038637`
- fully_eligible_fixture_count: 1
- minimum_required: 300
- remaining: 299
- coverage_gate_pass: false
- sample_frozen: false
- independent_oos_authorized: false
- scientific_gate: `OOS_SAMPLE_COVERAGE_CLOSED_1_OF_300`
- target labels / training / scoring / tuning / Provider / paid Provider / formal_weight: 全部 0

## 已完成、禁止重复

- 不重做首场 Alaves-Getafe bundle。
- 不重跑已经 PASS 的 R45B data readiness 或重新设计已冻结 OOS prereg。
- 不把 SHOTS_SOT_PROXY 写成 true xG。
- 不把第一场 query-time research baseline 升级为正式市场快照。
- 不读取 target labels、训练、评分、调参、结果后补数据、付费 Provider、改正式模型/权重/config/CURRENT。
- 普通“继续”不等于 independent OOS 标签/训练授权。

## 刷新/断线后的唯一恢复逻辑

1. 先读 `ACTIVE_CHECKPOINT.md` 和 Airtable 唯一激活《执行检查点》。
2. 若状态 WAITING/COMPLETED：从 `恢复位置/唯一下一步` 继续，不重做“已完成事项”。
3. 若状态 RUNNING/VERIFYING：先查操作ID对应副作用是否真实发生；禁止直接重试。
4. 只有明确未发生且 `可安全重试=true` 才重试；不确定则 BLOCKED。
5. 再用本文件、PROJECT_CURRENT、Airtable 当前状态/维护日志和必要时唯一CURRENT交叉核验。

## 唯一下一步

- 本轮治理结束后保持 R45B WAITING。
- 用户后续明确继续 R45B 时：先把 `recIRxK7EIMjJdG4A` 更新为 RUNNING（checkpoint_version+1），写入具体第2个 fixture 子步骤和唯一操作ID，再执行采集。
- 每个 fixture bundle 完成并通过 validator 后，检查点写 COMPLETED/WAITING + 真实证据，再推进下一个 fixture。
- 达到 >=300 且覆盖门通过后才允许冻结 sample identity；之后仍需单独明确 OOS 授权才能读标签/训练/评分。

## 权威优先级

用户当前明确指令 > 唯一正式 CURRENT（正式规则事项） > ACTIVE_CHECKPOINT实时断点 > LAST_HANDOFF对话末端 > PROJECT_CURRENT + Airtable状态验证 > 历史聊天/旧文件/记忆。
