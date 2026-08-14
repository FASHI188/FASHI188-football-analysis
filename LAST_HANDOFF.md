# 足球项目最后对话接力点

> 实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 22
- state_version: 53
- updated_at_utc: 2026-08-14T13:46:00Z
- status: BLOCKED

## 用户最后指令

用户要求“开始”，即从已核验的R55B执行断点继续，不能重做科学设计。

## 已完成科学状态

R55固定300硬OU投影已经完成并正式发布state53：
- Direct-T LL 1.79696725 -> 1.79789309，变差 +0.00092584；
- HDA LL 1.04789111 -> 1.04854976，变差 +0.00065866；
- natural Top1 Draw 0 -> 0；
- OU对T>=3排序AUC 0.58545804，高于1X2-only Direct-T 0.57250850；
- 固定裁决 `FAIL_R55_HARD_OU_PROJECTION_NO_STABLE_INCREMENT`。

科学判断保持：OU存在正交排序信息，但100%硬投影注入过强/校准不匹配。

## R55B 冻结设计

- branch: `research/r55-ou-matched300-20260814`
- prereg: `football-data/research/r55b_pretarget_ou_alpha_prereg_20260814.json`
- target n=300
- sample hash=`7f874277290f3c9664425f80e5281c18feddea85ff7306ed8ae64e6f6d949ffb`
- 单一全局 alpha ∈ [0,1]
- alpha 只能使用 2016-03-01 前严格历史OU重叠窗学习
- 目标300禁止参与alpha选择
- 校准n<300则STOP
- 冻结alpha后只允许一次应用固定300
- forced draw / Top-k / class weight / 人工1-1奖励全部禁止
- 2025/26 confirmation仍关闭
- formal_weight=0

## 最新执行核验

2026-08-14 20:48+08 跟进发现：
- R55B prereg 已存在；
- 没有任何R55B alpha calibration/evaluation Actions run；
- run_id absent；job_id absent；result absent；
- workflow目录只有R55零标签匹配入口，没有R55B执行workflow；
- 因此此前checkpoint31的RUNNING是假运行状态，实际dispatch从未发生。

已完成纠正：
- Airtable唯一激活执行检查点 -> checkpoint32 / BLOCKED；
- side_effect -> VERIFIED_ABSENT；
- Airtable current_state -> BLOCKED / checkpoint32；
- PROJECT_CURRENT与本LAST_HANDOFF同步到checkpoint32。

## 唯一下一步

保持R55B冻结科学设计和固定300完全不变：

1. 在现有研究分支补齐可持久化R55B runner；
2. 补齐可手动dispatch的R55B workflow；
3. 使用现有历史OU数据，不访问付费Provider；
4. 真实dispatch；
5. **只有拿到concrete run_id/job_id后才能把检查点重新标RUNNING**；
6. 得出alpha/result后按预注册门裁决；
7. 如产生科学PASS/FAIL/STOP，先发布新state_version，禁止直接进入下一实验。

## 禁止重复

- 不重做R55固定300抽样；
- 不改sample hash；
- 不改alpha定义/范围/目标函数；
- 不在固定300调权重；
- 不重复R55硬投影；
- 不调用付费Provider；
- 不打开2025/26确认窗；
- 不改正式模型、权重、config或CURRENT。

## 关键锚点

- unique CURRENT: V5.2.0 / count=1
- main before checkpoint32 sync: `98be2fc0da72074e59eaaf16ed01537d69f05aaf`
- R55 branch: `research/r55-ou-matched300-20260814`
- R55 prereg: `aeb5d4f615c11a7521059234acf8dc850b4bda81`
- R55 result: `ff96f9b7f33d189995d68bcd2c6304cae1c5cc76`
- R55B prereg blob: `ed365d820788d13c36813d114e29631e27bf1998`
- current_state: `recs1pQ1rhuwJQAzE`
- state53 log: `recU6blnhI73XIRWo`
- execution checkpoint: `recIRxK7EIMjJdG4A`
- checkpoint_version: 32

## 恢复硬门

研究前必须确认：
`PROJECT_CURRENT.state_version = LAST_HANDOFF.state_version = Airtable当前状态.state_version = 激活检查点.state_version = 53`

并且：
`Airtable当前状态.执行检查点版本 = 激活检查点.checkpoint_version = 32`

通过后才允许构建并dispatch R55B执行链。
