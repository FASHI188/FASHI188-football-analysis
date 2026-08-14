# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 52
- updated_at_utc: 2026-08-14T09:30:00Z
- updated_by: GPT-5.6 Sol
- status_source: `STATE52_R55_ARCHIVE_DATA_GATE_FAIL_NO_OU`
- status: WAITING
- state_log_record_id: `rectVxackbVvNDUxq`

## 正式规则状态

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0
- formal model/data/config/CURRENT changes in state52: 0
- all research below remains formal_weight=0

## 接续治理状态

state51已完成第二次接续失步治理，并新增“实质科学结论前置发布硬门”：
任何新的PASS/FAIL/STOP/封存、瓶颈变化、研究主线变化或数据门变化产生后，下一研究原子步骤启动前必须完成新的state_version双边发布；否则停止为 `BLOCKED_STATE_PUBLICATION_LAG`。

本state52严格按该门执行：R55数据门产生新结论后，没有继续下一实验，而是先发布state52。

## R55：同一archive的独立OU数据门

用户明确要求继续使用本轮上传 `archive (1)(1).zip` 找300场试验。

### 零标签审计

本轮在冻结样本前只读取：
- ZIP文件名；
- CSV表头；
- match_id / date / time / league / home_team / away_team；
- 32家1X2在grid71的H/D/A完整度。

明确未读取：
- `score_home`
- `score_away`
- `home_score`
- `away_score`
- `score`
- `detailed_score`
- 任何其他赛果/比分标签

archive SHA-256:
`8bb898c3c067dfca4c4e50f7e0fb3f01104b52a1d748c27ea7973ec96b36cab1`

archive内仅有5个文件：
1. `closing_odds.csv.gz`
2. `odds_series.csv.gz`
3. `odds_series_b.csv.gz`
4. `odds_series_b_matches.csv.gz`
5. `odds_series_matches.csv.gz`

schema审计结果：
- `closing_odds.csv.gz`: 只有closing 1X2及其平均/最大/来源数量信息；
- `odds_series.csv.gz`: 32家×72网格的H/D/A 1X2轨迹；
- `odds_series_b.csv.gz`: 同类32家×72网格H/D/A轨迹；
- 两个matches文件只提供比赛身份与结果字段；
- OU / Over / Under / totals / O-U 价格字段数量：**0**。

因此该archive不能提供R55要求的“独立赛前OU总进球市场”。

### 300场零标签冻结

固定规则：
- source: `odds_series_b.csv.gz`
- start date: `2016-04-01`
- grid71至少8家同时存在有效Home/Draw/Away报价；
- 按 `match_date, match_time, match_id` 升序；
- 取前300场；
- 不使用赛果进行筛选。

冻结结果：
- sample count: 300
- 时间窗: `2016-04-01 00:30:00` 至 `2016-04-02 14:00:00`
- 联赛数: 125
- sample SHA-256:
`0954c122748d93576f96bbd3b526c4b4ecff7fec3ecd1f26ee3538bdb1a91ce3`
- zero-label manifest SHA-256:
`ed7b1a6b8be7cabb58b2dd86799df31d1f11eeab0e575f19a42eaed4f445e784`

### 数据门裁决

`R55_ARCHIVE_DATA_GATE_FAIL_NO_OU`

因为R55的唯一新增信息族必须是与1X2正交的真实OU/Total数据，而本archive的OU覆盖为0：

- target labels opened: 0
- model fits: 0
- scoring runs: 0
- threshold search: 0
- forced draw / Top-k: 0

没有为了“凑一个300场结果”把1X2开放度、轨迹或同源微观结构重新命名成OU。

## 保留的科学结论

- R35-R38：Direct-T -> 条件D=0仍是当前唯一具有较稳定概率层正证据的平局结构；
- R52 Oracle定位：主要瓶颈在赛前 `P(T=0..7+)` 的质量与锐度；
- R53：1X2开放度轴有微弱排序信息，但不足以解决自然Top-1平局；
- R54：同包1X2轨迹 + 球队/联赛历史残差叠加明确FAIL；
- 因此下一条有信息增量意义的路线必须引入真实独立OU/Total或严格赛前机会量信息。

## 唯一下一步

R55模型验证尚未执行。

如果继续R55：
1. 必须获得真实赛前OU/Total数据（优先OU2.5，最好有多线OU）；
2. 该OU数据必须与冻结/新冻结比赛身份严格匹配；
3. OU只可修正已有Direct-T prior或作为可审计市场约束，不能人工拆出0-7+分布；
4. 再用互斥时间前推样本验证 Direct-T LL、HDA/Draw proper score、自然Top-1 Draw precision/recall；
5. 未获得OU前，不重复R53/R54同源1X2实验。

## 当前禁止事项

- 不把同源1X2开放度或轨迹冒充OU；
- 不在冻结300场上做结果后阈值搜索、forced draw、Top-k、class weight或人工比分奖励；
- 不打开新的2025/26确认窗；
- 不调用付费Provider；
- 不修改正式模型、正式数据、正式config或CURRENT；
- 不把本数据门FAIL写成OU模型已经测试失败。

## Airtable锚点

- base: `足球项目接续`
- current_state_record: `recs1pQ1rhuwJQAzE`
- state52 maintenance_log: `rectVxackbVvNDUxq`
- execution_checkpoint: `recIRxK7EIMjJdG4A`
- checkpoint_version: 29

## 权威优先级

当前用户明确指令 > 唯一正式CURRENT > ACTIVE_CHECKPOINT实时协议与唯一激活检查点 > LAST_HANDOFF > PROJECT_CURRENT + Airtable当前状态/绑定维护日志 > 历史聊天/旧文件/记忆。
