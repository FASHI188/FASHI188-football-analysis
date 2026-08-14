# 足球项目最后对话接力点

> 实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 17
- state_version: 46
- updated_at_utc: 2026-08-14T01:19:00Z
- status: WAITING

## 当前真实断点

- 唯一CURRENT：V5.2.0，未修改。
- 最新研究支线：`research/draw-pit-availability-zero-label-20260814`
- latest persisted research HEAD：`2091fbd1e67bc581bf77525b076d3966a8adabef`
- 既有draw研究分支：`research/draw-subtype-mechanism-300-20260813` / HEAD `b80cb24edc73ba7aa80d4a6c97e3207d92b32cac`
- Draft PR #191：Open / Draft / Unmerged / formal_weight=0。
- 正式模型、正式数据、正式config、CURRENT改动=0。

## 必须恢复的既有科学结论

### 正证据

1. R24成熟历史fresh300概率确认PASS：LL delta -0.0088098，Draw AUC +0.0293944，CI90全<0。
2. R35 Direct-T 0-7+ -> 条件D=0 fresh300 core PASS：LL -0.0044166，AUC +0.0148452，CI90全<0。
3. R36/R37 fresh300及R38 fresh1000方向总体与R35一致，但单独bootstrap门未全部通过。
4. R35-R38非重叠1900场合并诊断：LL -0.0025010，AUC +0.0092975，Brier -0.0016548，date-block CI90 [-0.0044489,-0.0005596]。

### 必须保留的反证

1. R27B-R33 Top-1/选择性执行连续fresh验证未复制。
2. R34 32-book盘口微观结构明确FAIL。
3. R39固定 `qD/market_pD` Top30：7中，precision23.33%、recall2.78%、整体准确率-0.8pp；FAIL并封存。

## state46新增进展

### R40：历史PIT伤停/可用性数据门

- 公开FPL历史Git快照可恢复真实赛前PIT。
- 2020/21-2024/25共1,738场满足零标签PIT可用门，五季均>=150。
- zero-label run `31726018581` success；Artifact `9191280711`。
- R40静态伤停存量候选明确FAIL：619行，HDA LL +0.030129，Draw AUC -0.050148，bootstrap90全为正。
- 裁决：静态伤停数量/静态攻击缺口不再继续。

### R41：可用性“变化冲击”稀疏Top-1假设

- 不用静态存量，只比较上一份PIT快照到本场PIT快照的状态变化：新伤、恶化、恢复、攻击角色可用性变化。
- transition PIT约1,686场可恢复。
- 当前探索诊断为2023/24+2024/25共609条已消费标签，因此不是独立OOS确认。
- 全局HDA LL +0.002107、Draw AUC -0.002534，概率层门未过。
- 但自然argmax出现25次Top-1 Draw，命中11次：precision44.0%、recall8.27%。
- 2023/24为10报5中；2024/25为15报6中。
- 未forced Top-k、未结果后阈值搜索。

## 当前裁决

`STATIC_AVAILABILITY_FAILS_BUT_AVAILABILITY_CHANGE_MAY_BE_SPARSE_TOP1_GATE_NOT_CONFIRMED`

最准确表述：
- 平局概率结构已有实质进展，但Top-1仍未正式解决。
- 静态伤停存量已被否定。
- “赛前可用性短期变化”第一次出现值得第三时间段验证的稀疏Top-1信号。
- 25报11中/44%不能写成独立确认或可用模型，因为609条标签已被消费且全局LL/AUC未改善。
- R41必须冻结，禁止在当前609条上调阈值、权重、特征或forced Top-k。

## 唯一下一步

若用户继续研究：

1. 先找第三个未参与R41选择的时间段；
2. 只做零标签覆盖门；
3. 保持同一候选、同一特征、同一PIT门；
4. 原样复核；
5. 覆盖不足就放弃该时间段，不放宽 `>=6h` 且 `<=10天陈旧`。

2025/26公开FPL历史只有约12个players_raw提交，第三时间段覆盖是否足够尚未完成零标签审计。

## 本轮治理边界

- 当前用户只要求把项目进展修齐，避免成果丢失。
- 本轮不继续研究、不读新标签、不训练、不评分、不调参。
- formal model/data/config/CURRENT change=0；formal_weight=0。

## Airtable恢复锚点

- current_state `recs1pQ1rhuwJQAzE` / state46
- maintenance log `recV6fQUxqADTqycH`
- execution checkpoint `recIRxK7EIMjJdG4A` / state46 / WAITING

## 权威优先级

用户当前明确指令 > 唯一正式CURRENT > ACTIVE_CHECKPOINT实时断点 > LAST_HANDOFF > PROJECT_CURRENT + Airtable当前状态/绑定维护日志 > 历史聊天/旧文件/记忆。
