# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 40
- updated_at_utc: 2026-08-13T12:16:00Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_R45B_HIST300_ODDS_TRAJECTORY_FAIL
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态（最高优先级）

- status: WAITING
- latest_completed_task: 用户指定的历史300场平局开发实验
- latest_research_branch: `research/r45b-hist300-odds-trajectory-20260813`
- latest_research_head: `3cb6fe9ee859552d1b90d5b3cc01694214344d21`
- latest_ruling: `HISTORICAL_ODDS_TRAJECTORY_DRAW_MASS_FAIL`
- full_R45B_forward_branch: `research/r45b-pit-role-availability-zero-label`
- full_R45B_forward_head: `c5625410463436c2d2bfa10b67634d92644dfdc6`
- full_R45B_forward_progress: 2 / 300
- full_R45B_forward_remaining: 298
- independent_oos_authorized: false
- formal_model_change: 0
- formal_data_change: 0
- config_change: 0（正式配置）
- CURRENT_change: 0

## 正式规则状态

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0
- CURRENT_change_this_state: 0

## 用户上传历史数据包

- local_archive_name: `archive (1)(1).zip`
- archive_sha256: `8bb898c3c067dfca4c4e50f7e0fb3f01104b52a1d748c27ea7973ec96b36cab1`
- files: `closing_odds.csv.gz`, `odds_series.csv.gz`, `odds_series_b.csv.gz`, `odds_series_matches.csv.gz`, `odds_series_b_matches.csv.gz`
- current_hist300_source: `odds_series.csv.gz`
- source_rows: 31,074
- note: 该包有盘口轨迹和最终比分/赛果，但没有完整R45B所需的预计首发/角色、伤停替代、过程能力、任务状态字段。因此可用于独立历史开发轨，但不得冒充完整R45B训练集。

## 历史300场固定实验

- schema: `R45B-HIST300-ODDS-TRAJECTORY-R1`
- seed: 20260813
- fixed_checkpoints: 24 / 48 / 71
- minimum_complete_books_each_checkpoint: 3
- eligible_pool_count: 15,513 / 31,074
- sample_n: 300
- sample_selection: 仅按固定盘口特征完整性筛选后等概率reservoir抽样；赛果值不参与eligibility或抽样
- split: 按时间排序；119初始训练 + 59 / 60 / 62 三个顺序OOS折；内部边界不拆同日期
- candidate_id: `HIST300_DRAW_MASS_LOGIT_OFFSET_ODDS_TRAJECTORY_R1`
- baseline: checkpoint71 多book完整H/D/A去水共识
- candidate: `logit(qD)=logit(pD_base)+beta0+beta^Tz`；剩余H/A质量保持baseline H:A比例
- l2_lambda: 1.0
- fixed_features: 8个盘口轨迹/分歧/平衡/overround/book-count特征
- candidate_search: false
- threshold_tuning: false
- post_label_feature_selection: false

## 历史300场结果

- pooled_oos_test_n: 181
- actual_draw_count: 41
- baseline_multiclass_logloss: 0.9683258939
- challenger_multiclass_logloss: 1.0028327349
- multiclass_logloss_delta_challenger_minus_baseline: +0.0345068410（恶化）
- multiclass_logloss_delta_90pct_calendar_date_block_bootstrap_ci: `[+0.0115370444, +0.0584115405]`
- baseline_draw_binary_logloss: 0.5265898216
- challenger_draw_binary_logloss: 0.5610966626
- draw_binary_logloss_delta: +0.0345068410（恶化）
- draw_binary_logloss_delta_90pct_ci: `[+0.0115370444, +0.0584115405]`
- baseline_draw_auc: 0.5768292683
- challenger_draw_auc: 0.4973867596
- baseline_accuracy: 54.1436%
- challenger_accuracy: 51.9337%
- baseline_top1_draw_count: 0
- challenger_top1_draw_count: 7
- challenger_top1_draw_hits: 1
- challenger_top1_draw_precision: 14.2857%
- challenger_top1_draw_recall: 2.4390%
- folds_multiclass_ll_improved: 0 / 3
- folds_draw_binary_ll_improved: 0 / 3
- ruling: **FAIL / SEALED_ON_THIS_300**

## GitHub研究证据

- branch: `research/r45b-hist300-odds-trajectory-20260813`
- exact_head: `3cb6fe9ee859552d1b90d5b3cc01694214344d21`
- script: `football-data/research/r45b_hist300_odds_trajectory_r1.py`
- result: `football-data/research/r45b_hist300_odds_trajectory_result.json`
- row_level_oos_predictions: `football-data/research/r45b_hist300_odds_trajectory_oos181.csv`
- full_local_300_prediction_sha256: `9787527f4160db6313532c5682442022a9ebd3567184147e033299cfe01de03a`
- formal_weight: 0

## 历史300场裁决

- 这次不是“平局已经会预测”。
- 仅盘口轨迹直接修正平局质量，在固定300场上明确失败：proper-score、AUC、accuracy全部恶化，且3个OOS折0/3改善。
- 不能因为Top-1 Draw从0变7就视为进步；7场仅命中1场。
- 这条candidate在同一300场上封存，禁止结果后继续搜索特征、调阈值、换参数或反复训练。
- 若继续历史开发，必须换新的信息源/特征家族并先固定新预注册；不得回炉使用本181场OOS结果进行调参。

## 完整R45B前向轨保持不变

- manifest_status: OPEN_ZERO_LABEL_ACCUMULATING
- fully_eligible_fixture_count: 2
- minimum_required: 300
- remaining_to_minimum: 298
- coverage_gate_pass: false
- sample_frozen: false
- authorization_ready: false
- independent_oos_authorized: false
- target_match_labels_read: 0
- training_runs: 0
- scoring_runs: 0
- tuning_runs: 0
- provider_requests: 0
- paid_provider_requests: 0
- formal_weight: 0
- completed_fixture_1: Alaves–Getafe
- completed_fixture_2: Sevilla–Rayo
- next_forward_fixture_if_resumed: #3

## 当前硬禁区

- 不在同一历史300场上做结果后candidate search、feature selection、threshold tuning、参数搜索或反复重训。
- 不把本历史300场写成 independent OOS、正式晋级或完整R45B效果。
- 不删除或覆盖完整R45B前向2/300进度。
- 完整R45B达到>=300并通过赛事覆盖门前，不得冻结前向sample identity；冻结后仍需单独明确independent OOS授权。
- 不调用付费Provider，不改正式模型、formal_weight、正式config或CURRENT。
- 总进球旁路继续暂停。

## 新对话恢复顺序

- START_HERE唯一版 → ACTIVE_CHECKPOINT协议 → Airtable执行检查点 → LAST_HANDOFF → PROJECT_CURRENT → Airtable当前状态及绑定维护日志 → 必要时唯一CURRENT。
- 不恢复state38旧START_HERE清理阻塞。
- 不重复本历史300场候选。

## Airtable同步锚点

- airtable_base: 足球项目接续
- current_state_record_id: `recs1pQ1rhuwJQAzE`
- state_log_record_id: `reckuEjWvGrNAe8EM`
- execution_checkpoint_record_id: `recIRxK7EIMjJdG4A`
- airtable_state_version: 40
