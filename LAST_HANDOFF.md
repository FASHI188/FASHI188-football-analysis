# 足球项目最后对话接力点

> 实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 16
- state_version: 45
- updated_at_utc: 2026-08-13T16:35:00Z
- status: WAITING

## 当前真实断点

- 唯一CURRENT：V5.2.0，未修改。
- 研究分支：`research/draw-subtype-mechanism-300-20260813`
- research HEAD：`b80cb24edc73ba7aa80d4a6c97e3207d92b32cac`
- Draft PR：#191 / Open / Draft / Unmerged / formal_weight=0
- 最新研究总结：`football-data/research/draw_r34_r39_new_family_adjudication.md`
- 正式模型、正式数据、正式config、CURRENT改动=0。

## 当前必须恢复的科学结论

### 正证据

1. R24成熟历史fresh300概率确认PASS：LL delta -0.0088098，Draw AUC +0.0293944，CI90全<0。
2. R35采用新的 Direct-T 0-7+ -> 条件D=0结构，fresh300 core PASS：LL -0.0044166，AUC +0.0148452，CI90全<0。
3. R36/R37 fresh300和R38 fresh1000虽然各自bootstrap core未过，但LL、AUC、Brier均与R35同方向改善。
4. R35-R38非重叠1900场合并诊断：LL -0.0025010，AUC +0.0092975，Brier -0.0016548，date-block CI90 [-0.0044489,-0.0005596]。

### 反证

1. R27B-R33的Top-1/选择性平局执行连续fresh验证失败；R33 fresh1000 Top20只有5中，precision25%。
2. R34 32-book盘口微观结构fresh300明确FAIL：LL +0.0078691，AUC -0.0351843，CI90全>0。
3. R39为T条件链单独冻结的高置信执行确认：fresh1000，固定 `qD/market_pD` Top30，只有7中，precision23.33%、recall2.78%、准确率-0.8pp；同一晚期窗口概率层也轻微退化（LL +0.0005393、AUC -0.0015597）。

## 当前裁决

`T_CONDITIONED_DRAW_PROBABILITY_SIGNAL_POOLED_REPLICATED_BUT_NONSTATIONARY_TOP1_NOT_VALIDATED`

最准确表述：
- 平局概率结构研究已经取得实质进展：直接把Draw当单标签不如“分机制 + Direct-T -> 条件D=0”。
- 但Top-1平局或低覆盖高置信平局仍未形成可复制执行规则。
- 不得把概率改善说成“平局已经研究出来/Top-1已可用”。
- R34-R39以及此前所有已查看Top-1样本禁止继续结果后调权重、阈值、selector。

## 下一步

若用户继续要求攻平局，直接切换到**新PIT信息家族**，不回炉当前archive selector：
- 完整预计首发/角色；
- 伤停与替代质量；
- 赛前过程事件/机会质量；
- 或完整R45B历史PIT重建。

当前archive自身可支持的市场/历史赛果/状态转移/总进球条件结构已经做过强约束验证，继续调同源selector会构成适应性过拟合。

## 完整R45B前向轨仍保持

- branch `research/r45b-pit-role-availability-zero-label`
- HEAD `c5625410463436c2d2bfa10b67634d92644dfdc6`
- fully eligible 2/300；remaining 298
- independent_oos_authorized=false
- labels/training/scoring/tuning/Provider/paid Provider/formal_weight 全部0

## Airtable恢复锚点

- current_state `recs1pQ1rhuwJQAzE` / state45
- maintenance log `recrxbfWLFr8VVfXC`
- execution checkpoint `recIRxK7EIMjJdG4A` / state45 / WAITING

## 权威优先级

用户当前明确指令 > 唯一正式CURRENT > ACTIVE_CHECKPOINT实时断点 > LAST_HANDOFF > PROJECT_CURRENT + Airtable当前状态/绑定维护日志 > 历史聊天/旧文件/记忆。
