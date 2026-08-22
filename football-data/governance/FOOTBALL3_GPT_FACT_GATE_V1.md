# 足球3 GPT 事实核验硬门 V1.0

> 状态：`ACTIVE_GPT_FACT_BOUNDARY`
>
> 正式位置：`football-data/governance/FOOTBALL3_GPT_FACT_GATE_V1.md`
>
> 本文件约束事实表述与证据边界，不替代唯一正式 CURRENT，也不自动授权训练、标签访问、Provider、晋级、Ready、发布或合并。

## 1. 强制读取顺序

1. 读取 GitHub 工作树内唯一的 `CURRENT_唯一正式规则.md`；数量不等于 1、路径不符或无法完整读取时立即停止。
2. 完整读取本事实核验硬门。
3. 核对当前用户指令、repository、branch、exact HEAD、PR、run、job 和 Artifact。
4. 历史聊天、本机文件、Airtable、粘贴回执和旧报告仅作线索，不得覆盖 GitHub 实时事实。
5. 完成结构化核验后，才可使用“已完成、已通过、正在运行、已停止、可以使用”等确定措辞。

## 2. 只允许四种事实分类

- `VERIFIED`：由实时 GitHub/API、确定性复算或 SHA 绑定 Artifact 直接支持；必须给出准确身份、来源和观测时间。
- `REPORTED_NOT_VERIFIED`：来自其他对话、人工回执或历史记录；只能表述为尚未独立核实的转述。
- `INFERRED`：由已核实事实推导；必须明确写成推断，并给出依据、局限和替代解释。
- `UNKNOWN`：缺证据、身份冲突、状态过期、未执行或无法区分；不得补猜。

任何关键事实缺证据、身份不一致、超过适用时效或相互冲突时，必须停止在 `UNKNOWN` / `REPORTED_NOT_VERIFIED`。

## 3. 必须实时核验的事实

- PR 是否 Open、Draft、Ready、merged、closed，以及当前 exact HEAD；
- workflow run 和每个 job 的 `status` 与 `conclusion`；
- Artifact ID、名称、所属 run、绑定 HEAD、摘要和内部身份；
- 研究是否真正执行，工程门与科学门分别是什么结果；
- 标签访问数、首次访问时间、Provider/Secret 使用、训练、调参和真实评分；
- `formal_weight`、模型、正式数据、config、CURRENT 是否改变；
- 是否真实执行发布、Ready 或合并。

动态状态必须带 `observed_at_utc`；超过 24 小时的状态不得继续写成“当前”。

## 4. 准确身份链

确定结论必须绑定同一条身份链：

`repository -> branch/PR HEAD -> run HEAD -> job run_id -> Artifact run/HEAD -> evidence SHA`

任一环断裂，整条结论降级为 `UNKNOWN`。

## 5. 禁止错误等号

- workflow success 不等于科学效果 PASS；
- Artifact 存在不等于内容正确或模型有效；
- job 出现不等于 job 已执行；
- `queued` 不等于正在计算；
- `skipped` 不等于已验证；
- 代码提交不等于 exact-HEAD Actions 已通过；
- preflight PASS 不等于研究已启动或结束；
- 单一窗口或局部指标改善不等于长期稳定有效；
- engineering/research PASS 不等于 `MODEL_IMPROVED`、`DRAW_SOLVED` 或正式晋级；
- 普通 GPT 分析不等于足球3引擎输出。

## 6. 标准核验流程

1. 采集实时原始证据并记录身份与时间；
2. 绑定 repository、HEAD、run、job、Artifact 和摘要；
3. 逐项分类为 VERIFIED、REPORTED_NOT_VERIFIED、INFERRED 或 UNKNOWN；
4. 区分工程执行结果与科学效果门；
5. 写出最窄结论和仍未授权事项；
6. 写操作后立即回读；超时先检查副作用，禁止盲目重试。

## 7. GPT 固定执行指令

每次处理足球3项目，必须先读取 GitHub 唯一 CURRENT 和本事实核验硬门。任何关于 PR、HEAD、Actions、Artifact、队列、训练、评分、指标、标签、Provider、正式资产、Ready、发布或合并的陈述，必须逐条分类。没有实时证据不得冒充 VERIFIED。准备使用“已完成、已通过、正在运行、问题已解决、可以开始、可以晋级或可以合并”等确定措辞前，必须完成结构化核验。核验通过也不自动授予后续权限。

## 8. GitHub-only 边界

- 本文件和唯一 CURRENT 的 GitHub 版本是正式版本；
- 本机、聊天、Airtable 或上传副本均为非权威缓存；
- 正式治理修改必须产生 GitHub commit SHA 和 exact-HEAD 远端验收；
- 无 GitHub commit 与远端验收时，一律视为未落实；
- GitHub App 读取仓库不等于真实运行引擎；网页 GPT 必须通过真实执行接口才能返回模型输出；
- 宁可明确写 `UNKNOWN`，也不得用肯定语气包装未经核实的内容。
