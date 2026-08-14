# 足球项目最后对话接力点

> 实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 19
- state_version: 51
- updated_at_utc: 2026-08-14T09:21:00Z
- status: WAITING

## 用户最后指令

用户要求把第二次出现的接续状态不同步彻底“修齐”，并说明为什么会重复发生。当前只做治理修复，不启动新的R55实验。

## 本轮实际发现

第二次失步的直接证据：

- 唯一CURRENT仍为V5.2.0，数量1，无版本冲突；
- GitHub `PROJECT_CURRENT.md` 与 `LAST_HANDOFF.md` 停在state47；
- Airtable《当前状态》也停在state47；
- Airtable《当前状态》保存的执行检查点版本为21；
- 唯一激活《执行检查点》实际已经到version27；
- 维护日志已经实际推进R51、R52、R53、R54。

所以问题不是研究丢了，而是**高频层和低频正式接续层脱节**。

## 为什么会第二次发生

原协议虽然写了“重要状态变化要更新PROJECT_CURRENT/LAST_HANDOFF”，但没有在“下一研究原子步骤启动前”做机器式硬门。

因此出现了这条错误路径：

`R51完成 -> 写维护日志/检查点 -> 直接R52 -> R53 -> R54`

而没有强制执行：

`实质科学结论 -> state publication -> 回读一致 -> 才能下一研究步骤`

结果维护日志和实时检查点越走越前，正式接续层仍旧停在state47。

## 本轮修复

1. 新建绑定维护日志state51：`recrFkhQsfbpetHcw`。
2. 唯一激活检查点进入修复事务，checkpoint_version=28。
3. `ACTIVE_CHECKPOINT.md`加入“实质科学结论前置发布硬门”。
4. `PROJECT_CURRENT.md`升级到state51，并正式写入R51-R54真实科学状态。
5. `LAST_HANDOFF.md`升级到state51，恢复点固定在R54完成/R55未开始。
6. state51完成后，任何新的PASS/FAIL/STOP/封存/瓶颈变化/唯一研究方向变化，下一研究步骤启动前必须先做state publication；否则 `BLOCKED_STATE_PUBLICATION_LAG`。

## 当前科学接力点

### R52

Oracle定位显示：给定真实总进球T后，现有条件D|T结构能自然产生大量平局；主要瓶颈收敛到赛前P(T)质量与锐度，而不是继续强攻平局阈值。

### R53

1X2开放度轴有微弱排序价值，但同源1X2信息不足以把P(T)锐化到自然Top-1平局执行层。

### R54

- branch: `research/draw-r54-direct-t-rolling900-20260814`
- commit: `5654a015746e06e33580299052949132db34c7d9`
- sample hash: `d23cc3fe92d8461490d63b1402a8a7ac15ceea2b2f316df390d4543a260ef61b`
- 900场/242平
- candidate T LL 1.832234 vs old 1.830746，变差+0.001488
- HDA LL 0.989507 vs market0.987263，变差+0.002244
- Draw AUC 0.566204 vs market0.581970，变差-0.015766
- 自然Top1平局7报2中，precision28.57%、recall0.83%
- HDA LL bootstrap90% [0.000982,0.004186]，明确有害
- Oracle真实T仅用于定位：305报平/193中，precision63.28%、recall79.75%、overall HDA accuracy63.44%

固定裁决：`FAIL_R54_ARCHIVE_ONLY_NO_BREAKTHROUGH_DO_NOT_CONFIRM`。

**最新科学结论：同一1X2轨迹包已经基本榨干；真正缺的是独立赛前总进球/机会量信息。**

## 唯一下一步

R55尚未开始。

下一研究方向固定为：

`独立OU市场 -> OU residual / Total Surprise -> Direct-T prior修正 -> 条件D|T -> 自然H/D/A`

优先研究：

- 去水OU2.5绝对水平；
- OU开盘到收盘变化；
- Pinnacle/Bet365/Avg/Max等OU分歧；
- `OU信息 - 1X2隐含开放度` 的正交Total Surprise；
- 若历史数据有多线OU，进一步约束总进球CDF；
- 第二顺位才是严格赛前射门/射正/xG/机会质量等PIT机会量。

OU不得人工拆分制造0-7+分布，只能修正已有Direct-T prior或作为可审计约束。

## 当前禁止事项

- 不继续在R50/R51/R53/R54同窗调阈值、forced draw、Top-k、class weight或人工比分奖励；
- 不继续从同一1X2轨迹包堆复杂模型；
- 不把Oracle结果写成可预测结果；
- 不读取新的2025/26确认标签；
- 不调用付费Provider；
- 不修改正式模型、正式数据、正式config或CURRENT；
- DeepSource/Sonar治理暂停；PR #192保持Draft/Open/Unmerged。

## 新对话恢复硬门

启动研究前必须确认：

`PROJECT_CURRENT.state_version = LAST_HANDOFF.state_version = Airtable当前状态.state_version = 激活检查点.state_version = 51`

并且：

`Airtable当前状态.执行检查点版本 = 激活检查点.checkpoint_version`

如不一致，不得研究，固定停止为 `BLOCKED_STATE_PUBLICATION_LAG`。

## Airtable恢复锚点

- current_state: `recs1pQ1rhuwJQAzE`
- state51 maintenance log: `recrFkhQsfbpetHcw`
- execution checkpoint: `recIRxK7EIMjJdG4A`

## 权威优先级

用户当前明确指令 > 唯一正式CURRENT > ACTIVE_CHECKPOINT实时协议与唯一激活检查点 > LAST_HANDOFF > PROJECT_CURRENT + Airtable当前状态/绑定维护日志 > 历史聊天/旧文件/记忆。
