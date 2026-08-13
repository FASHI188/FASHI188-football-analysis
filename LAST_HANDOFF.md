# 足球项目最后对话接力点

> 本文件只保存“上一条对话末端如何继续”的最新接力信息。它不是正式规则，不替代 `PROJECT_CURRENT.md`、Airtable 当前状态或唯一 CURRENT。每次有实质进展、用户改变方向、发生阻塞或准备结束当前回答时，都必须覆盖更新本文件。

## 接力身份

- project_id: football-project
- handoff_version: 8
- state_version: 36
- updated_at_utc: 2026-08-13T09:24:21Z
- status: WAITING

## 用户最后明确目标

- 用户本轮明确说“继续”。
- 已从 state35 继续完成 R45B 免费公开源、前向、零标签研究链。
- 当前不是等待用户重复授权做日常采集；唯一科学边界是：普通“继续”绝不等于 independent OOS authorization。

## 本轮已经完成

1. 按唯一 CURRENT V5.2.0 完成启动验收；CURRENT 数量=1，旧版执行权=0。
2. `task_utility`：固定 extractor 已实现；两队各 1 条严格赛前 schedule_fatigue record；validator 硬断言通过。
3. `availability_and_replacement`：Lucas Boyé 伤情 + Mariano / Aitor Mañas 明确替代候选链形成 1 条合法 record；validator 通过。
4. `process_capability`：预注册并实现只读 HS/HST/AS/AST 的 `SHOTS_SOT_PROXY` extractor；两队各最近 10 场，比分/赛果列禁用；两条 record 持久化并通过 validator。首次字段合同写错的失败保留，后按正式 forward contract 修正，未放宽 validator。
5. `expected_xi_roles`：同一 freeze `2026-08-13T09:07:14Z` 建立两队各 11 人 role-assigned XI；阵型 Alaves 4-4-2、Getafe 5-3-2；球员 role 只用来源明确的标准 position，不从阵型人工推本场 LCB/RWB 等部署。
6. 最终 forward validator 得到 7 records / 7 valid / 0 invalid；expected_xi_roles=2、availability=1、process=2、task=2；两队同 freeze role-XI 配对=1；五轴全 true，blocking_axes=[]；状态 `READY_FOR_SEPARATE_OOS_PREREGISTRATION`。
7. hardened data-readiness Actions：run `31685408164` / job `94400180975` completed/success；完整 readiness 硬断言与 OOS guard 均 success。
8. 创建独立 OOS 零标签预注册 `r45b_independent_oos_preregistration.json`，SHA-256=`b91ff8547151aacebc94aa91bb82f4425de7974b8c09b943c214f63d8065e159`。
9. OOS 设计已锁：唯一候选 `R45B_DRAW_MASS_LOGIT_OFFSET_R1`；同 freeze 完整 1X2 no-vig baseline；>=300 fully eligible；约 120 初始训练块 + 3 顺序 OOS folds；主检验 HDA LogLoss，Draw binary LogLoss 必须同步改善；2000 次日期块 bootstrap；禁止结果后候选搜索/阈值调参/feature selection。
10. OOS prereg 首次 workflow 因 validator 把合法整数 0 当 falsey 的 bug 失败；只修 validator bug，不改 prereg 内容。复验 run `31686029804` / job `94402185269` completed/success；13项必填=13/13；candidate_count=1；sample_gate=300；test_folds=3；OOS 仍未授权。
11. 为首场 Alaves-Getafe 捕获研究 1X2 baseline：FanDuel +130 / +190 / +240，decimal 2.30 / 2.90 / 3.40，no-vig H/D/A≈0.404928/0.321150/0.273922；只有 collector-first-observed 时间，无 native quote timestamp，因此 `formal_market_snapshot=false`、只作研究 baseline。
12. 创建 `r45b_oos_sample_manifest.json`，开始 300 场零标签 OOS 样本积累；首场 bundle freeze=`2026-08-13T09:20:03Z`，target label=`UNREAD_FORBIDDEN`。
13. 创建 sample manifest validator：逐场检查 7份PIT证据、bundle freeze、两队 XI 同 freeze、单一 bookmaker 完整 1X2、去水概率、重复 fixture、禁用 label keys、coverage 数学和零标签 invariant。
14. first-sample workflow run `31686297563` / job `94403038637` completed/success；`fully_eligible=1/300`，coverage 仍强制关闭，sample 未冻结，authorization_ready=false，OOS guard success。
15. 本轮 target labels=0、training=0、scoring=0、tuning=0、Provider=0、paid Provider=0、formal_weight=0；正式模型/正式数据/正式配置/CURRENT 改动=0。
16. 已写 Airtable state36 维护日志 `recAqUN9Mgn7Guztm`；PROJECT_CURRENT 与 LAST_HANDOFF 正在同步 state36。

## 当前项目真实停点

- task: R45B 独立 OOS 研究样本前向积累
- branch: `research/r45b-pit-role-availability-zero-label`
- exact_head: `028cbced2db6c8b7046114e36e1f09184e6c9a3d`
- data_readiness: PASS
- data_readiness_run/job: `31685408164 / 94400180975`
- oos_preregistration: PASS
- prereg_run/job: `31686029804 / 94402185269`
- oos_sample_manifest: OPEN_ZERO_LABEL_ACCUMULATING
- first_sample_validation: PASS
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

- 不要再回到“R45B 能否获得 PIT 信息”的可得性争论：单场完整 bundle 已以 7/7 valid、五轴全 true 证明可执行。
- 不要重新设计候选、样本门、指标或阈值：独立 OOS prereg 已机器 PASS，后续必须按 SHA 绑定。
- 不要把第一场市场 baseline 升级成正式市场快照；它缺 native quote timestamp 且聚合页赛事标题 taxonomy 有警告，只是 prospective query-time research baseline。
- 不要把 `SHOTS_SOT_PROXY` 写成 true xG。
- 不要把球员标准 position 夸成精确本场战术 role；本场部署不做人工推断。
- 不得读取目标赛果标签、训练、评分、调参、结果后补数据、付费 Provider、正式模型/权重/config/CURRENT改动。
- 不得把普通“继续”解释成 OOS 授权。
- 旧 `足球2.txt` 继续 `DEPRECATED_LEGACY_ENTRY`、execution_weight=0。

## 唯一下一步

- 按冻结的 `R45B-INDEPENDENT-OOS-PREREG-R1` 继续**前向、零标签**积累剩余 299 个完全合格 fixture bundle。
- 每场必须在开球前满足：合同级 role-XI、availability/replacement、process、task、matchup gate + 同 bundle-freeze 完整单一-bookmaker 90分钟1X2 research baseline。
- 缺任何轴、只能结果后补、市场三项不完整、报价来源时点不合格，都整场排除；不得人工补概率或降门槛。
- 只有 fully eligible >=300 且赛事覆盖门通过，才允许冻结 sample identity manifest。
- 冻结 sample identity 后仍必须取得**单独明确 independent OOS authorization**，才允许读取 target labels、训练或评分。

## 权威优先级

用户当前明确指令 > 唯一正式 CURRENT（正式规则事项） > LAST_HANDOFF（对话末端） > PROJECT_CURRENT + Airtable（状态验证） > 历史聊天/旧文件/记忆。
