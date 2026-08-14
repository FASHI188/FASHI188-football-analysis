# 足球项目最后对话接力点

> 实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；长期启动/长任务规则读取 `AGENTS.md`；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 24
- state_version: 53
- updated_at_utc: 2026-08-14T16:10:00Z
- status: BLOCKED

## 用户最后指令

用户要求解决“让GPT干一个较大的事，一条对话很快就满”的问题。要求继续遵守最小治理：有真实问题才修，不为完善而强改。

## 本轮治理结果

已确认原治理只有“断线后恢复”，没有“对话达到上限前主动换挡”的硬门。现做最小持久化修复：

1. `AGENTS.md` 新增第11节“长任务防对话溢出协议”；
2. 长任务改为可恢复执行块，每块开始/结束使用现有 Airtable《执行检查点》，不新增表或字段；
3. 优先精确文件/字段/run/job/关键词查询，避免无必要整库、整PR、整日志读取；
4. 连续完成两个实质执行块，或下一阶段明显属于大日志/大Artifact/批量处理时，完成当前块后主动形成安全换对话点，不再硬塞第三个重阶段；
5. 新对话只需说“继续”，从唯一检查点的“唯一下一步”直接恢复，不要求重述任务、不重跑已完成事项；
6. 模型无法可靠读取ChatGPT UI精确剩余上下文，因此使用行为门提前留余量，不伪造token百分比。

本治理只改变任务分段与上下文使用方式；CURRENT、模型、数据、config、研究权限和R55B科学设计均0改动。

## 当前研究真实断点

R55B仍为research-only，模型计算尚未启动。

已恢复：
- calibration 475条完整记录；
- calibration-min 471条完整记录；
- 两者均止于2015-11-28；
- fixed target300独立OU逐场输入已从Artifact `9215167178`完整恢复。

仍缺：
- 2015-11-28之后至2016-02-29的pre-target archive grid71校准尾段；
- fixed target300对应archive grid71 `qH/qD/qA`。

## 唯一下一步

重新附加同一 `archive (1)(1).zip`，第一动作只校验SHA-256：

`8bb898c3c067dfca4c4e50f7e0fb3f01104b52a1d748c27ea7973ec96b36cab1`

- SHA不匹配：立即停止；
- SHA匹配：确定性补齐缺失pre-target tail与target300 archive grid71输入；
- 之后才落runner/workflow；
- 只有真实取得run_id/job_id后才能标记R55B RUNNING；
- 新科学PASS/FAIL/STOP产生后先发布新state_version。

## 禁止重复

- 不重新抽300；
- 不改sample hash或alpha定义/范围/目标函数；
- 不用471条截断集直接拟合；
- 不用Football-Data 1X2或近似公式替代archive grid71；
- 不在固定300调权重；
- 不调用付费Provider；
- 不打开2025/26确认窗；
- 不改正式模型、权重、config或CURRENT。

## 关键锚点

- unique CURRENT: V5.2.0 / count=1
- state_version: 53
- state53 creation log: `recU6blnhI73XIRWo`
- current_state: `recs1pQ1rhuwJQAzE`
- execution_checkpoint: `recIRxK7EIMjJdG4A`
- research branch: `research/r55-ou-matched300-20260814`
- research HEAD: `2e786c66cfd1678d592bb6e16f666eabf70db611`
- frozen target n: 300
- sample hash: `7f874277290f3c9664425f80e5281c18feddea85ff7306ed8ae64e6f6d949ffb`
- R55B prereg blob: `ed365d820788d13c36813d114e29631e27bf1998`

## 正式资产变化

- model: 0
- data: 0
- config: 0
- CURRENT: 0
- formal_weight: 0

## 恢复硬门

新对话先按AGENTS顺序核验四层状态；通过后直接从实时检查点继续。长任务同时执行AGENTS第11节：到安全换对话门时先落检查点再换对话，禁止等到上下文耗尽才写接力。
