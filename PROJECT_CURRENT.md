# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 42
- updated_at_utc: 2026-08-13T13:59:30Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_DRAW_SUBTYPE_SIGNAL_REPLICATED_2_OF_3_NOT_FORMAL

## 当前状态（最高优先级）

- status: WAITING
- latest_completed_task: 用户上传历史数据包上的平局分机制研究与三组300场复制验证
- latest_research_branch: `research/draw-subtype-mechanism-300-20260813`
- latest_research_head: `ab0764b89fde17af996707d48718a0def238cb3b`
- latest_research_pr: `#191` Draft / Open / Unmerged
- latest_ruling: `DRAW_SUBTYPE_SIGNAL_REPLICATED_2_OF_3_NOT_FORMAL`
- formal_weight: 0
- formal_model_change: 0
- formal_data_change: 0
- formal_config_change: 0
- CURRENT_change: 0

## 正式规则状态

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0

## 用户上传历史数据包

- archive: `archive (1)(1).zip`
- archive_sha256: `8bb898c3c067dfca4c4e50f7e0fb3f01104b52a1d748c27ea7973ec96b36cab1`
- files: `closing_odds.csv.gz`, `odds_series.csv.gz`, `odds_series_b.csv.gz`, `odds_series_matches.csv.gz`, `odds_series_b_matches.csv.gz`
- 本轮分机制研究使用 `closing_odds.csv.gz`，source rows = 479,440。

## 旧历史300盘口轨迹候选

- branch: `research/r45b-hist300-odds-trajectory-20260813`
- HEAD: `3cb6fe9ee859552d1b90d5b3cc01694214344d21`
- ruling: `FAIL / SEALED_ON_THIS_300`
- pooled OOS 181；multiclass LL delta +0.0345068；Draw AUC 0.576829 -> 0.497387；fold improvement 0/3。
- 禁止回炉该300场调参。

## 新平局分机制候选

- candidate: `DRAW-SUBTYPE-MECHANISM-R1`
- target classes: non-draw / 0-0 / 1-1 / 2-2 / 3-3+
- draw probability: sum of draw-subtype probabilities, then residual around no-vig closing-market pD while preserving H:A ratio in non-draw mass
- pre-match features: strict-prior cumulative team/league state + closing 1X2 market structure
- feature_count: 49
- training cap: 80,000 strictly earlier eligible matches
- candidate search after confirmation start: 0
- formal_weight: 0

### 300场窗口 A：2015-06-13..2015-06-19

- result: PASS
- actual draws: 72 / 300
- HDA LogLoss: 0.9646813 -> 0.9618955，delta -0.0027858
- Draw LogLoss: 0.5458345 -> 0.5430487
- Draw AUC: 0.5784600 -> 0.5862573
- accuracy: 54.33% -> 54.67%
- 90% calendar-date block bootstrap CI: `[-0.0042674, -0.0010880]`

### 300场窗口 B：2015-06-06..2015-06-12

- result: near-neutral FAIL
- actual draws: 75 / 300
- HDA LogLoss delta: +0.0000962
- Draw AUC: 0.6707556 -> 0.6594963
- accuracy: 49.67% -> 50.00%
- 90% CI: `[-0.0029275, +0.0009909]`

### 300场窗口 C：2015-05-30..2015-06-05

- result: PASS
- actual draws: 82 / 300
- HDA LogLoss: 0.9723148 -> 0.9682864，delta -0.0040285
- Draw LogLoss: 0.5677405 -> 0.5637120
- Draw AUC: 0.6525509 -> 0.6609420
- accuracy: 51.00% -> 51.00%
- 90% CI: `[-0.0088568, -0.0007849]`

### 当前科学裁决

- 同一候选/同一超参数在3个互不重叠300场窗口中 **2/3核心门PASS**。
- 这是当前平局研究中首次出现可复制的proper-score/AUC增量证据。
- 不能写成“平局问题已经解决”或“正式模型已晋级”。
- 当前增益主要体现在概率排序和校准；Top-1 Draw覆盖仍很低。
- 下一步不得回炉这900场调参；应冻结候选，在新的未触碰样本或独立赛事域确认，或另外预注册Top-1平局选择器研究。

## GitHub证据

- Draft PR: `#191`
- branch: `research/draw-subtype-mechanism-300-20260813`
- exact HEAD: `ab0764b89fde17af996707d48718a0def238cb3b`
- script: `football-data/research/draw_subtype_mechanism_300_20260813.py`
- script SHA-256: `b8d99d0de8660e7db30efea0e70e372c933530b0dcb533151bd709f6cfd9ca71`
- confirmation lock SHA-256 R1: `658e4b0e6b8541392a8d53d5c30fd4af2278f6b16bc74bfbc63797c2718959ab`
- confirmation lock SHA-256 R2: `16f68f24a85449f9ec1f783f2883515fea54f9ccdf12033b4f2d616fc4f0aca1`

## 完整R45B前向轨保持不变

- branch: `research/r45b-pit-role-availability-zero-label`
- HEAD: `c5625410463436c2d2bfa10b67634d92644dfdc6`
- fully eligible: 2 / 300
- remaining: 298
- independent_oos_authorized: false
- target labels / training / scoring / tuning / Provider / paid Provider / formal_weight: 全部0

## GitHub工程应用状态

- GitHub Actions: ACTIVE / VERIFIED_REAL_RUNS
- Renovate: ACTIVE / VERIFIED_BOT_PR
- DeepSource: GitHub App连接与事件接收已验证；`.deepsource.toml` 已在main；DeepSource check-run仍为0，真实分析执行未验证
- SonarQube Cloud / GitGuardian: 连接探针历史存在，但真实分析执行仍需单独验收

## 当前硬禁区

- 不回炉旧盘口轨迹300或本次3x300窗口做结果后参数/特征搜索。
- 不把2/3研究PASS写成正式模型晋级或完整平局问题解决。
- 不修改 formal_weight、正式模型、正式config或CURRENT。
- 不调用付费Provider。
- 总进球旁路继续暂停。

## 新对话恢复顺序

- START_HERE唯一版 -> ACTIVE_CHECKPOINT协议 -> Airtable执行检查点 -> LAST_HANDOFF -> PROJECT_CURRENT -> Airtable当前状态及绑定维护日志 -> 必要时唯一CURRENT。
- 新对话应优先恢复PR #191的分机制平局候选状态，不得退回“平局完全无增量”的state41结论。

## Airtable同步锚点

- airtable_base: 足球项目接续
- current_state_record_id: `recs1pQ1rhuwJQAzE`
- state_log_record_id: `recF5KnVP5bFBLW6I`
- execution_checkpoint_record_id: `recIRxK7EIMjJdG4A`
- airtable_state_version: 42
