# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 43
- updated_at_utc: 2026-08-13T16:22:00Z
- updated_by: GPT-5.6 Sol
- status_source: R24_MATURE_HISTORY_DRAW_TRANSITION_CONFIRM_PASS_TOP1_UNRESOLVED

## 当前状态（最高优先级）

- status: WAITING
- latest_completed_task: R24成熟历史区间平局转移概率盲确认
- latest_research_branch: `research/draw-subtype-mechanism-300-20260813`
- latest_research_head: `c12c14b8f65b716aadbc650f90d0c787ca0eb009`
- latest_research_pr: `#191` Draft / Open / Unmerged
- latest_ruling: `R24_MATURE_HISTORY_DRAW_TRANSITION_CONFIRM_PASS_TOP1_UNRESOLVED`
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
- 平局研究主要使用 `closing_odds.csv.gz`，source rows = 479,440。

## 已确认的平局研究链

### 三组独立300场分机制确认（state42）

同一候选/同一超参数在三组互不重叠300场上结果为：

1. 2015-06-13..2015-06-19：PASS。HDA LogLoss 0.9646813→0.9618955；Draw AUC 0.5784600→0.5862573；90%日期块bootstrap CI [-0.0042674,-0.0010880]。
2. 2015-06-06..2015-06-12：近中性 FAIL。HDA LogLoss delta +0.0000962；CI跨0。
3. 2015-05-30..2015-06-05：PASS。HDA LogLoss 0.9723148→0.9682864；Draw AUC 0.6525509→0.6609420；90% CI [-0.0088568,-0.0007849]。

裁决：2/3核心门通过，属于研究证据，不是正式晋级，Top-1 Draw覆盖仍很低。

### R21探索证据

- R21共六个300窗口均改善LL/AUC。
- 但R21预注册路径/日期字段存在治理错误，因此只能作探索证据，不能作为正式晋级依据。
- R21后续冻结为R23/R24所需的候选结构来源，不允许用已查看窗口继续结果后调参。

### R23早期历史确认

- history_n: 8,728
- 使用全新早期历史窗口严格预注册。
- HDA LogLoss delta: +0.0030867
- Draw AUC delta: -0.00873
- 置信区间跨0
- ruling: FAIL
- R23必须永久保留为失败证据，不得回炉该窗口调参或删除失败结果。

### R24成熟历史区间盲确认

- history_n: 30,395
- 先排除所有既往 `*_predictions.csv` 已出现的 match_id。
- 从4,060个未评分候选中以label-free哈希锁定全新300场。
- R21的68个特征及参数完全冻结。
- HDA LogLoss: 0.9624433 → 0.9536335
- HDA LogLoss delta: -0.0088098
- Draw LogLoss: 0.5189301 → 0.5101202
- Draw AUC: 0.5653028 → 0.5946972
- 90%日期块bootstrap CI: [-0.0173351,-0.0015384]
- prereg core gate: PASS
- Top-1状态：candidate argmax仅1场平局且未命中，Top-1执行层仍未解决。

### state43科学裁决

- R24是当前第一组真正满足“严格预注册 + 未评分match_id不重叠 + 成熟历史窗口”的300场确认PASS。
- 这支持“概率质量增量存在”，不支持“平局问题已解决”。
- R23 FAIL与R24 PASS必须同时保留，不能选择性报告。
- R21/R24概率候选冻结，不得在R24的300场上调阈值、调参数或做candidate search。
- 当前唯一剩余主问题是：如何把R24已经确认的概率优势转化为可实际报出的Top-1平局，同时守住precision、recall和整体1X2准确率。
- 本次state43接续治理不启动任何新的足球研究、样本读取、训练、评分或调参。

## GitHub证据

- Draft PR: `#191`
- branch: `research/draw-subtype-mechanism-300-20260813`
- exact HEAD: `c12c14b8f65b716aadbc650f90d0c787ca0eb009`
- PR state: Open / Draft / Unmerged
- formal_weight: 0
- R24 evidence source: Airtable维护日志 `recGS4AUjdYQwKwX6`
- R24结果文件/summary已存在于研究分支；本次治理不修改研究分支内容。

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

- 不重复R23或R24。
- 不在R24这300场或其他已查看窗口上调参数、阈值或做candidate search。
- 不把R24 PASS写成正式模型晋级、平局问题已解决或完整1X2达到某个高胜率。
- 不修改 formal_weight、正式模型、正式config或CURRENT。
- 不调用付费Provider。
- 总进球旁路继续暂停。
- 本次接续治理禁止启动新的研究场次。

## 新对话恢复顺序

- START_HERE唯一版 -> ACTIVE_CHECKPOINT协议 -> Airtable执行检查点 -> LAST_HANDOFF -> PROJECT_CURRENT -> Airtable当前状态及绑定维护日志 -> 必要时唯一CURRENT。
- 新对话恢复时必须识别state43，并同时看到R23 FAIL、R24 PASS和Top-1未解决。
- 若Airtable与GitHub状态版本再次不一致，必须先治理接续一致性，不得自行选择一个版本继续研究。

## Airtable同步锚点

- airtable_base: 足球项目接续
- current_state_record_id: `recs1pQ1rhuwJQAzE`
- state_log_record_id: `recGS4AUjdYQwKwX6`
- execution_checkpoint_record_id: `recIRxK7EIMjJdG4A`
- airtable_state_version: 43
- execution_checkpoint_version_at_sync_start: 13
