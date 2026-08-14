# 足球项目最后对话接力点

> 实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 21
- state_version: 53
- updated_at_utc: 2026-08-14T10:15:00Z
- status: WAITING

## 用户最后指令

用户要求继续开干，用 `archive (1)(1).zip` 找300场尝试突破平局/Direct-T。

## 本轮已经完成

1. state52/checkpoint29一致性门通过后启动R55，checkpoint_version=30。
2. 从archive零标签提取英格兰四级联赛grid71身份候选；未在冻结前读目标赛果。
3. 找到独立Football-Data 2015/16 OU2.5源，并固定四个Git blob SHA。
4. Actions run `31790299125` 严格按日期+主客队匹配OU：400候选中340严格匹配，冻结前300。
5. frozen300 sample hash=`7f874277290f3c9664425f80e5281c18feddea85ff7306ed8ae64e6f6d949ffb`；时间窗2016-03-01至2016-04-16。
6. 在打开目标标签前提交R55评分预注册：commit `aeb5d4f615c11a7521059234acf8dc850b4bda81`。
7. 打开冻结300标签后完成Direct-T baseline与独立OU硬KL投影对比；result commit=`ff96f9b7f33d189995d68bcd2c6304cae1c5cc76`。
8. 结果形成实质科学FAIL后，没有继续调OU权重，先发布state53。

## R55固定300结果

- 实际H/D/A=120/88/92。
- Direct-T baseline LL=1.79696725。
- Direct-T hard-OU LL=1.79789309；delta +0.00092584（变差）。
- Direct-T RPS 0.11963025 -> 0.11957135（极小改善）。
- HDA LL 1.04789111 -> 1.04854976；delta +0.00065866（变差）。
- Draw LL +0.00065866；Brier +0.00026808；AUC 0.50734 -> 0.48987。
- natural Top1 Draw 0 -> 0。
- 90% bootstrap Direct-T LL delta [-0.0055223,0.0072826]；HDA LL delta [-0.0012598,0.0025594]。

固定裁决：
`FAIL_R55_HARD_OU_PROJECTION_NO_STABLE_INCREMENT`

## 最重要的新发现

独立OU不是完全没信息：同一300场的 `T>=3` 排序：

- 1X2-only Direct-T group mass AUC = 0.57250850
- independent OU AUC = 0.58545804

但OU二元LogLoss 0.67911110，略差于Direct-T group mass 0.67818525。

因此最新判断是：
**OU含有正交排序信息，但100%硬替换总进球组质量过强/校准不匹配。**

这使下一问题从“OU有没有用”转成“OU residual应该以多大、怎样校准的强度注入Direct-T”。

## 唯一下一步

R55B / 下一研究原子步骤：

`目标300之前的历史OU重叠窗 -> 学习固定OU residual强度/校准 -> 冻结 -> 一次性应用同一300 -> Direct-T -> 条件Draw -> 自然H/D/A`

硬约束：
- 权重/校准只能从目标300之前历史学习；
- 禁止在当前300搜索最佳权重；
- 禁止forced draw、Top-k、class weight；
- 若前置历史不足以稳定学习参数则STOP；
- 仍research-only、formal_weight=0；
- 不打开2025/26确认窗，不用付费Provider，不改CURRENT或正式资产。

## 关键锚点

- unique CURRENT: V5.2.0, count=1
- archive SHA256: `8bb898c3c067dfca4c4e50f7e0fb3f01104b52a1d748c27ea7973ec96b36cab1`
- R55 branch: `research/r55-ou-matched300-20260814`
- zero-label run: `31790299125`
- zero-label Artifact: `9215167178`
- prereg commit: `aeb5d4f615c11a7521059234acf8dc850b4bda81`
- result commit: `ff96f9b7f33d189995d68bcd2c6304cae1c5cc76`
- current_state: `recs1pQ1rhuwJQAzE`
- state53 log: `recU6blnhI73XIRWo`
- execution checkpoint: `recIRxK7EIMjJdG4A`
- checkpoint_version: 30

## 新对话恢复硬门

研究前必须确认：
`PROJECT_CURRENT.state_version = LAST_HANDOFF.state_version = Airtable当前状态.state_version = 激活检查点.state_version = 53`

并且：
`Airtable当前状态.执行检查点版本 = 激活检查点.checkpoint_version = 30`

不一致则固定停止：`BLOCKED_STATE_PUBLICATION_LAG`。
