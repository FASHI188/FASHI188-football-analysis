# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 45
- updated_at_utc: 2026-08-13T16:34:00Z
- updated_by: GPT-5.6 Sol
- status_source: `T_CONDITIONED_DRAW_PROBABILITY_SIGNAL_POOLED_REPLICATED_BUT_NONSTATIONARY_TOP1_NOT_VALIDATED`
- status: WAITING

## 正式规则状态

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0
- formal model/data/config/CURRENT changes in state45: 0
- formal_weight for all research below: 0

## 当前研究分支

- branch: `research/draw-subtype-mechanism-300-20260813`
- Draft PR: #191 / Open / Draft / Unmerged
- latest research HEAD: `b80cb24edc73ba7aa80d4a6c97e3207d92b32cac`
- latest summary: `football-data/research/draw_r34_r39_new_family_adjudication.md`

## 用户历史数据

- archive: `archive (1)(1).zip`
- archive_sha256: `8bb898c3c067dfca4c4e50f7e0fb3f01104b52a1d748c27ea7973ec96b36cab1`
- relevant sources: `closing_odds.csv.gz`, `odds_series.csv.gz`, `odds_series_b.csv.gz`, mapping files

## 已保留的正证据

### R24成熟历史概率确认

- fresh300 / history_n=30,395
- HDA LL 0.9624433 -> 0.9536335, delta -0.0088098
- Draw AUC 0.5653028 -> 0.5946972
- bootstrap90 LL delta [-0.0173351,-0.0015384]
- probability gate PASS
- Top-1 Draw仍未解决

### R35-R38 Direct-T -> 条件D=0结构链

固定结构：直接预测 P(T=0,1,2,3,4,5,6,7+)，再预测条件 P(D=0|T,X)，最后自然汇总平局概率；不人工增加平局、不人工指定总进球分布。

- R35 fresh300: core PASS；LL delta -0.0044166；AUC delta +0.0148452；bootstrap90 [-0.0085493,-0.0019548]
- R36 fresh300: LL -0.0041018；AUC +0.0028750；CI跨0，core FAIL/near-pass
- R37 fresh300: LL -0.0015684；AUC +0.0020704；CI跨0，core FAIL/near-pass
- R38 fresh1000: LL -0.0017259；AUC +0.0115026；CI跨0，core FAIL/directional
- R35-R38 pooled 1900 diagnostic: LL delta -0.0025010；AUC delta +0.0092975；Brier delta -0.0016548；date-block bootstrap90 [-0.0044489,-0.0005596]
- pooled audit was computed after individual tests, therefore diagnostic rather than a preregistered promotion gate.

## 必须保留的反证

### Top-1 / selective execution family

R27B-R33连续fresh验证未能复制Top-1平局执行。R33 fresh1000 precision-first Top20仅5中：precision 25%，recall 2.22%，FAIL。

### R34 32-book market microstructure

- fresh300
- LL delta +0.0078691
- AUC delta -0.0351843
- bootstrap90 [0.0004084,0.0168185]
- clear FAIL / sealed

### R39 T-conditioned high-confidence selector

- fresh1000
- frozen rule: `qD / market_pD` top30 = 3% coverage forced Draw, others market argmax
- selected30 / hits7
- precision 23.33%
- recall 2.78%
- combined accuracy delta -0.80pp
- probability layer in this later window also weakened: HDA LL delta +0.0005393; AUC delta -0.0015597
- selector gate and probability guard both FAIL

## state45科学裁决

`T_CONDITIONED_DRAW_PROBABILITY_SIGNAL_POOLED_REPLICATED_BUT_NONSTATIONARY_TOP1_NOT_VALIDATED`

- 平局概率建模已找到比直接Draw/Not-Draw更有依据的结构：分机制 + Direct-T -> 条件D=0。
- 该结构在多个非重叠窗口方向一致，1900场合并诊断显著改善proper score/AUC/Brier。
- 但R39证明该增量并非在所有晚期窗口稳定，且无法转化为可复制Top-1/高置信平局执行。
- 因此不得写成“平局问题已解决”“Top-1平局已可用”或正式晋级。
- R34-R39及此前Top-1 selector样本均禁止结果后继续调权重/阈值。
- 下一步若继续，必须换更丰富PIT信息家族：完整首发/阵容角色、伤停与替代质量、赛前过程事件数据，或完整R45B历史PIT重建；不得继续在当前archive上做selector搜索。

## 完整R45B前向轨保持不变

- branch: `research/r45b-pit-role-availability-zero-label`
- HEAD: `c5625410463436c2d2bfa10b67634d92644dfdc6`
- fully eligible: 2 / 300
- remaining: 298
- independent_oos_authorized: false
- target labels / training / scoring / tuning / Provider / paid Provider / formal_weight: 全部0

## 其他边界

- 总进球正式轨仍暂停；R35-R38 Direct-T仅为平局研究挑战层，不代表正式总进球主轨晋级。
- 不调用付费Provider。
- GitHub工程App不得作为足球预测效果证据。

## 新对话恢复

START_HERE唯一版 -> ACTIVE_CHECKPOINT -> Airtable执行检查点 -> LAST_HANDOFF -> PROJECT_CURRENT -> Airtable当前状态/绑定维护日志 -> 唯一CURRENT。

恢复时必须看到 state45，并同时保留：R24/R35概率正证据、R34/R39失败反证、Top-1仍未验证。

## Airtable锚点

- base: 足球项目接续
- current_state_record: `recs1pQ1rhuwJQAzE`
- maintenance_log: `recrxbfWLFr8VVfXC`
- execution_checkpoint: `recIRxK7EIMjJdG4A`
- airtable_state_version: 45
