# 足球项目最后对话接力点

> 实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 14
- state_version: 42
- updated_at_utc: 2026-08-13T14:07:00Z
- status: WAITING

## 当前研究结论

- 用户要求继续用其上传历史文件研究平局，测试规模固定300场。
- 数据源：`archive (1)(1).zip` / `closing_odds.csv.gz`，约479,440场。
- 已冻结新候选：将平局拆为 `0-0 / 1-1 / 2-2 / 3-3+` 等不同机制，而非直接 Draw/Not-Draw。
- 研究分支：`research/draw-subtype-mechanism-300-20260813`
- HEAD：`ab0764b89fde17af996707d48718a0def238cb3b`
- Draft PR：#191，Open/Draft/未合并，formal_weight=0。

## 三组独立300场确认

1. 2015-06-13..2015-06-19：PASS。HDA LogLoss 0.9646813→0.9618955；Draw AUC 0.5784600→0.5862573；90%日期块bootstrap CI [-0.0042674,-0.0010880]。
2. 2015-06-06..2015-06-12：近中性 FAIL。HDA LogLoss delta +0.0000962；CI跨0。
3. 2015-05-30..2015-06-05：PASS。HDA LogLoss 0.9723148→0.9682864；Draw AUC 0.6525509→0.6609420；90% CI [-0.0088568,-0.0007849]。

同一候选/同一超参数在三组300场上结果为 **PASS / 近中性FAIL / PASS = 2/3核心门通过**。这是当前平局研究第一次出现跨独立300窗口可复制的概率增量信号，但仍不等于完整平局问题解决，也未获得正式权重。

## 下一步

- 冻结分机制候选，不在已查看900场上调参数、阈值或做candidate search。
- 下一步研究目标：把分机制概率优势转化为更有覆盖率的 Top-1 平局选择。
- Top-1选择器若开发，必须只用更早历史训练数据确定规则，并在新的未触碰300场上验证；不得回炉使用上述900场调阈值。
- 完整R45B前向轨仍保持2/300，不覆盖、不重做。
- 总进球旁路继续暂停。

## 硬边界

- 不调用付费Provider。
- 不修改正式模型、formal_weight、正式config或CURRENT。
- 不把2/3研究PASS写成正式晋级。
- 不重复PR #191。

## 权威优先级

用户当前明确指令 > 唯一正式CURRENT（正式规则事项） > ACTIVE_CHECKPOINT实时断点 > LAST_HANDOFF > PROJECT_CURRENT + Airtable当前状态/绑定维护日志 > 历史聊天/旧文件/记忆。
