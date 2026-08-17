# 足球项目当前状态

## 身份

- project_id: `football-project`
- state_version: **60**
- updated_at_local: `2026-08-17 15:52 +08:00`
- updated_by: `GPT-5.6 Sol`
- status_source: `USER_GOVERNANCE_DECISION_GOLD_STANDARD_RESERVE_RETIRED`
- status: `WAITING`
- state_log_record_id: `recIuJT7e3EN4oxC2`
- predecessor_state: 59

## 正式规则与正式资产

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- formal model/data/config/CURRENT changes in state60: **0**
- formal_weight changes in state60: **0**

state60 仅发布研究治理路线变更，不改变既有实验数值、历史裁决或正式资产。

## 当前研究事实

OU2.5 → Direct-T 的现有复制状态保持：

- B01：VIEWED，点估计改善，但严格 date-block bootstrap CI 跨 0；
- B02：VIEWED，严格单包门 PASS；
- B03：VIEWED，点估计改善，但严格 date-block bootstrap CI 跨 0；
- 三包 `delta LogLoss` 点估计均为负向改善，但不能据此事后发明新的组合确认门；
- B04：不再作为“金标准”候选。其旧流程结果字段物化/可见性问题继续保留为治理注记，不得因改名获得更高证据等级。

既有 post-view diagnostics、availability、multi-OU 等历史研究事实继续按原证据保留；state60 不重新判定其科学结论。

## state60 永久治理裁决：废止“金标准 reserve”

状态：**`RETIRED_PERMANENTLY`**

治理文件：`governance/GOLD_STANDARD_RESERVE_RETIRED.md`

自 state60 起：

1. 不再新建 `gold-standard reserve` / `金标准 reserve` / `gold-standard holdout` 或同义路线；
2. 不再把 B04 或任何既有 reserve 重新包装、重命名为“金标准”；
3. 不再把“建立新金标准包”作为用户需要选择的研究分叉或默认 ACTIVE_NEXT；
4. 不再把“target-only query 没请求标签”单独视为最高等级盲测的充分条件；
5. 旧聊天、旧日志、旧检查点、旧文档中出现的“新建金标准 reserve”建议全部降为历史废弃方案，不得恢复；
6. 后续独立验证必须逐项写清 PIT、标签隔离、样本选择、冻结时点、功效/样本量、预注册、OOS/forward 属性，不再用“金标准”一词替代这些条件。

## 当前唯一下一步

回到用户在本次治理前已指定的研究主线：**底座解剖 / 功效分析**。

优先只使用已经 VIEWED 的 B01-B03 做诊断性分析，不把结果重新包装成 confirmatory：

1. 分解 date-cluster 方差与有效样本量；
2. 基于已观察 effect size 估计不同样本规模下的统计功效；
3. 检查报价陈旧/时间异步是否解释方差或效应；
4. 检查跨联赛/日期簇异质性；
5. 不因诊断结果对已 VIEWED 包后验调参后重新宣称独立确认。

## 当前禁止事项

- 禁止恢复、新建或重命名任何 gold-standard / 金标准 reserve；
- 禁止把 B04 包装为金标准或最高等级确认包；
- 禁止把旧“B04或新gold-standard二选一”恢复为当前下一步；
- 禁止对 B01/B02/B03 事后调参后重新作为 holdout；
- 禁止事后发明 B01-B03 组合确认门并宣称 confirmatory；
- 禁止修改 formal model/data/config/CURRENT；
- 禁止把治理裁决写成新的科学 PASS。

## 轻量接续

新聊天恢复时：上一工作对话末尾小段（若可得）→ Airtable《当前状态》→唯一《执行检查点》→《会话接力》→核验 GitHub main。任何旧摘要或旧日志若仍建议“新建金标准 reserve”，以 state60 永久退役裁决覆盖。

## Airtable 锚点

- base: `足球项目接续` (`appLXF9IBvSCEUjJV`)
- current_state_record: `recs1pQ1rhuwJQAzE`
- state60 creation log: `recIuJT7e3EN4oxC2`
- execution_checkpoint_record: `recIRxK7EIMjJdG4A`
- checkpoint_version: **126**

## 权威优先级

当前用户明确指令 > 唯一正式 CURRENT > 最新 Airtable state/checkpoint/handoff > GitHub 当前状态文件 > 历史聊天/旧文件/记忆。
