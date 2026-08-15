# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 54
- updated_at_utc: 2026-08-15T17:24:00Z
- updated_by: GPT-5.6 Sol
- status_source: `STATE54_R55B_PRETARGET_ALPHA_FROZEN_WEAK_TEMPORAL_STABILITY`
- status: WAITING
- state_log_record_id: `recSphqybYbluBFEN`

## 正式规则状态

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0
- formal model/data/config/CURRENT changes in state54: 0
- R55/R55B remain research-only; formal_weight=0

## 接续治理

state51建立的“实质科学结论前置发布硬门”继续生效：新的PASS/FAIL/STOP、研究瓶颈变化、恢复锚点或唯一下一方向变化产生后，必须先完成新的state_version双边发布，再开始下一研究原子步骤。

state54发布的是R55B **pre-target alpha冻结**，不是固定300结果，更不是正式模型PASS。

## R55固定结论（保持不变）

- R55 result commit: `ff96f9b7f33d189995d68bcd2c6304cae1c5cc76`
- frozen target n=300
- frozen target sample hash=`7f874277290f3c9664425f80e5281c18feddea85ff7306ed8ae64e6f6d949ffb`
- hard-OU verdict: `FAIL_R55_HARD_OU_PROJECTION_NO_STABLE_INCREMENT`
- independent OU在T>=3排序上有一定正交信息，但100%硬投影校准/强度不合适。

不得把R55写成模型PASS，不得把旧精选70.77%@24.50%覆盖写成完整1X2 70%+，不得声称平局问题已解决。

## R55B 冻结设计

- research branch: `research/r55-ou-matched300-20260814`
- current research HEAD: `1ef58235de14071387d6e6382237b51f72c0cd6c`
- prereg: `football-data/research/r55b_pretarget_ou_alpha_prereg_20260814.json`
- prereg blob: `ed365d820788d13c36813d114e29631e27bf1998`
- alpha: exactly one global scalar in [0,1]
- fit data: strict pre-target historical overlap only, before `2016-03-01 00:00:00`
- objective: binary log loss for actual T>=3
- target300 cannot select/tune alpha
- target300 application only once after alpha freeze
- no per-league alpha / threshold search / forced draw / Top-k / class weight

## R55B 输入恢复（已闭合）

Authoritative Release archive:
- tag: `football-data-archive-v1`
- asset: `archive.1.1.zip`
- archive SHA-256: `8bb898c3c067dfca4c4e50f7e0fb3f01104b52a1d748c27ea7973ec96b36cab1`

Archive partition resolution:
- `odds_series.csv.gz`: 2015-09-01 to 2016-02-29
- `odds_series_b.csv.gz`: 2016-03-01 to 2016-11-20
- both have 6917 columns and the same 96 grid71 H/D/A columns for 32 bookmakers
- technical partition record: `football-data/research/r55b_archive_partition_resolution_20260816.json`

Successful recovery:
- run: `31896973713`
- job: `95041670919`
- Artifact: `9250066819`
- strict OU identities before grid gate: 1056
- complete calibration after >=8-bookmaker grid71 gate: 943
- calibration window: `2015-09-05 15:00:00` to `2016-02-29 20:45:00`
- minimum complete bookmakers: 13
- fixed target300 recovered: 300/300
- target grid71 missing: 0
- target hash: exact frozen hash
- target score access during recovery: 0
- persistent recovery result commit: `8c1932b96a2bece67b917da1fe58358d07db48ea`

## R55数值环境可复现性裁决

R55历史结果记录`direct_t_iterations=370`，但原prereg没有把精确solver iteration定义为科学身份门，且历史包版本未持久化。

独立技术probe证明同一19,422场、同一特征、同一LogisticRegression规格，在不同SciPy/sklearn数值栈下`n_iter_`可为360/362/396。因此精确迭代数是环境敏感元数据，不可覆盖预注册模型规格。

- resolution: `football-data/research/r55_baseline_iteration_fingerprint_resolution_20260816.json`
- resolution commit: `8be35e9beaac982446395adb3fce5793eb5ca405`
- scientific rule change: false
- replacement execution gate: exact training data fingerprint + exact feature/model spec + class order + convergence
- pinned R55B execution environment: Python3.13 / NumPy2.3.5 / SciPy1.17.0 / scikit-learn1.8.0
- environment was not selected using calibration alpha/loss or target300 performance

## state54：R55B pre-target alpha冻结结果

Execution:
- workflow head: `084d0f542f08c09d34523baf13efd14395f2dd50`
- run: `31898043150`
- job: `95044288985`
- Artifact: `9250346172`
- Artifact SHA-256: `24590c2b42db9e9f4ae72ab492ca75f637d301981efe5b1b43759612f94316e1`
- persistent result: `football-data/research/r55b_pretarget_alpha_fit_result_20260816.json`
- persistent result commit: `1ef58235de14071387d6e6382237b51f72c0cd6c`

Baseline fingerprint:
- training n=19,422
- date range=2005-01-01 to 2015-05-25
- T class counts=1522/3745/4914/4231/2714/1421/569/306
- exact R55 feature/model specification
- converged=true

Full pre-target calibration (n=943):
- actual T>=3 rate = 0.4984093319
- baseline mean P(T>=3) = 0.4756878092
- independent OU mean Over2.5 probability = 0.4770216201
- frozen global alpha = **0.27198933241466033**
- baseline binary LL = **0.688465669649886**
- fitted binary LL = **0.6883337105775551**
- LL gain (baseline - fitted) = **0.00013195907233087834**
- stop rule triggered = false
- ruling = `ALPHA_FROZEN_READY_FOR_TARGET_APPLICATION`

Chronological equal-count stability diagnostic:
1. n=315, 2015-09-05 to 2015-11-15: alpha=0.0772546587; LL gain=0.0000107384
2. n=314, 2015-11-15 to 2016-01-09: alpha=0.9999999700; LL gain=0.0025510688
3. n=314, 2016-01-09 to 2016-02-29: alpha=3.7377e-09; LL gain≈-6.58e-12

Scientific interpretation:
- full calibration has a positive but **very small** residual LL gain;
- temporal stability is **weak**: diagnostic block alphas span approximately 0 to 1;
- therefore this is only an alpha-freeze gate pass under the preregistered stop rule;
- it is **not** evidence that R55B is stable, not a model PASS, and not evidence that the draw problem is solved.

Target isolation remains intact:
- target rows used for alpha: 0
- target application performed: false
- target score access: 0
- target tuning: false

## 唯一下一研究方向

After state54 handoff sync is fully verified, and only then:

1. use the already frozen alpha `0.27198933241466033` unchanged;
2. apply it exactly once to the already frozen target300;
3. preserve Direct-T within-group ratios exactly as preregistered;
4. compute the same Direct-T / HDA / Draw metrics and bootstrap as R55;
5. do not search or revise alpha after target results are visible;
6. any resulting scientific PASS/FAIL/STOP must again be published as a new state_version before another experiment.

## 当前禁止事项

- 不重新拟合或搜索alpha；
- 不根据target300结果修改alpha；
- 不重新抽300或改sample hash；
- 不per-league alpha；
- 不threshold search / forced draw / Top-k / class weight；
- 不打开2025/26 confirmation；
- 不调用付费Provider；
- 不修改正式模型、正式数据、正式config或CURRENT；
- 不把state54称为R55B模型PASS或平局问题解决。

## Airtable锚点

- base: `足球项目接续`
- current_state_record: `recs1pQ1rhuwJQAzE`
- state54 creation log: `recSphqybYbluBFEN`
- execution_checkpoint_record: `recIRxK7EIMjJdG4A`
- realtime checkpoint_version: 以Airtable唯一激活记录为准

## 权威优先级

当前用户明确指令 > 唯一正式CURRENT > ACTIVE_CHECKPOINT实时协议与唯一激活检查点 > LAST_HANDOFF > PROJECT_CURRENT + Airtable当前状态/绑定维护日志 > 历史聊天/旧文件/记忆。
