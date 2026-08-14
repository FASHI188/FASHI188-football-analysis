# 足球项目最后对话接力点

> 实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 23
- state_version: 53
- updated_at_utc: 2026-08-14T15:56:00Z
- status: BLOCKED

## 用户最后指令

用户要求对足球项目做治理维护：只修可证明的问题；没有问题的地方不要强改，避免治理本身制造混乱。

## 本轮治理审计结论

确认两项真实接续问题，未发现需要扩大治理范围的其他硬错误：

1. **GitHub接力漂移**：Airtable实时断点已经完成R55B输入恢复穷尽审计，但GitHub `PROJECT_CURRENT.md` / `LAST_HANDOFF.md`仍停在更早的checkpoint32“dispatch gap”阶段，导致新对话可能恢复到旧位置。
2. **state53绑定日志被误覆盖**：Airtable《当前状态》的`状态日志记录ID`本应固定绑定建立state53的日志`recU6blnhI73XIRWo`，却被后来的R55B恢复维护日志`rec1LbQyn0bQzXrR3`覆盖。原state53建立日志仍完整存在。

未修改以下已经正确的治理规则：

- `AGENTS.md`；
- `ACTIVE_CHECKPOINT.md`；
- 唯一CURRENT；
- state_version=53；
- R55/R55B科学设计、固定300、sample hash、alpha定义和权限边界。

检查点`WAITING`与项目状态`BLOCKED`语义兼容：前者表示当前原子步骤等待外部输入，后者表示R55B项目级技术阻塞，因此不做字面统一。

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
- exact research HEAD: `2e786c66cfd1678d592bb6e16f666eabf70db611`
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

## 最新真实执行断点

R55B模型计算仍未启动。已完成的恢复工作为：

- calibration恢复475条完整记录；
- calibration-min恢复471条完整记录；
- 两者均止于2015-11-28；
- fixed target300独立OU逐场输入已从Artifact `9215167178`完整恢复；
- Git历史与Actions均未找到可无损替代的剩余archive grid71输入。

当前真实缺口：

- 2015-11-28之后至2016-02-29的pre-target archive grid71校准尾段；
- fixed target300对应archive grid71的`qH/qD/qA`。

因此不能用现有471条截断集直接拟合alpha，也不能用近似1X2替代。

## 唯一下一步

保持R55B冻结科学设计和固定300完全不变：

1. 重新附加同一 `archive (1)(1).zip`；
2. 第一动作只校验SHA-256，必须等于 `8bb898c3c067dfca4c4e50f7e0fb3f01104b52a1d748c27ea7973ec96b36cab1`；
3. SHA不匹配立即停止；
4. 匹配后确定性补齐缺失pre-target tail与target300 archive grid71输入；
5. 再落runner/workflow；
6. 真实dispatch且取得concrete run_id/job_id后才允许标记RUNNING；
7. 得出alpha/result后按原预注册门裁决；
8. 如产生新的科学PASS/FAIL/STOP，先发布新state_version。

## 禁止重复

- 不重新抽300；
- 不改sample hash；
- 不改alpha定义/范围/目标函数；
- 不用471条截断集直接拟合；
- 不用Football-Data 1X2或近似公式代替archive grid71；
- 不在固定300调权重；
- 不重复R55硬投影；
- 不调用付费Provider；
- 不打开2025/26确认窗；
- 不改正式模型、权重、config或CURRENT。

## 关键锚点

- unique CURRENT: V5.2.0 / count=1
- state_version: 53
- state53 creation log: `recU6blnhI73XIRWo`
- latest R55B recovery log: `rec1LbQyn0bQzXrR3`
- R55 branch: `research/r55-ou-matched300-20260814`
- research HEAD: `2e786c66cfd1678d592bb6e16f666eabf70db611`
- R55 prereg: `aeb5d4f615c11a7521059234acf8dc850b4bda81`
- R55 result: `ff96f9b7f33d189995d68bcd2c6304cae1c5cc76`
- R55B prereg blob: `ed365d820788d13c36813d114e29631e27bf1998`
- current_state: `recs1pQ1rhuwJQAzE`
- execution_checkpoint: `recIRxK7EIMjJdG4A`
- realtime checkpoint_version: 以Airtable唯一激活记录为准，不在本文件写死

## 正式资产变化

- model: 0
- data: 0
- config: 0
- CURRENT: 0
- formal_weight: 0

## 恢复硬门

新对话研究前至少确认：

- `PROJECT_CURRENT.state_version = LAST_HANDOFF.state_version = Airtable当前状态.state_version = 激活检查点.state_version = 53`；
- Airtable当前状态绑定的state53日志必须为`recU6blnhI73XIRWo`；
- Airtable当前状态的执行检查点版本必须等于唯一激活检查点实时版本；
- GitHub/Airtable的唯一下一步不得冲突。

通过后只能从原archive重附SHA校验处继续，不得退回更早的dispatch-gap阶段。
