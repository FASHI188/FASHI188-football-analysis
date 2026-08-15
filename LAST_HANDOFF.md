# 足球项目最后对话接力点

> 实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；长期启动/长任务规则读取 `AGENTS.md`；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 25
- state_version: 54
- updated_at_utc: 2026-08-15T17:25:00Z
- status: WAITING

## 用户最后指令

用户说“开始吧”，即继续既有R55B research-only授权，从已闭合的943场pre-target calibration进入唯一全局alpha拟合与冻结。不得重新设计实验、不得提前用fixed target300调参。

## 本轮已完成

### 1. R55B alpha执行链

- alpha runner: `football-data/research/r55b_pretarget_alpha_fit_20260816.py`
- persistent alpha result: `football-data/research/r55b_pretarget_alpha_fit_result_20260816.json`
- current research HEAD: `1ef58235de14071387d6e6382237b51f72c0cd6c`
- pinned numerical environment: Python3.13 / NumPy2.3.5 / SciPy1.17.0 / scikit-learn1.8.0

### 2. R55旧370迭代指纹技术裁决

R55结果曾记录`direct_t_iterations=370`。本轮没有直接删除该门，而是先做独立环境probe：同一19,422训练数据、同一特征、同一模型规格，在不同数值栈下得到360/362/396迭代。因此`n_iter_`是环境敏感求解器元数据，不是预注册科学身份。

- resolution: `football-data/research/r55_baseline_iteration_fingerprint_resolution_20260816.json`
- resolution commit: `8be35e9beaac982446395adb3fce5793eb5ca405`
- scientific rule change: 0
- replacement gate: exact training fingerprint + exact feature/model spec + class order + convergence
- environment selection did not use calibration alpha/loss or target300 result

### 3. alpha冻结真实结果

- run: `31898043150`
- job: `95044288985`
- Artifact: `9250346172`
- Artifact SHA-256: `24590c2b42db9e9f4ae72ab492ca75f637d301981efe5b1b43759612f94316e1`
- calibration n: 943
- frozen global alpha: `0.27198933241466033`
- baseline binary LL: `0.688465669649886`
- fitted binary LL: `0.6883337105775551`
- LL gain: `0.00013195907233087834`
- stop rule triggered: false
- ruling: `ALPHA_FROZEN_READY_FOR_TARGET_APPLICATION`

3段时间稳定性诊断：

1. 315场：alpha=`0.07725465867076434`，LL gain=`0.000010738423192435675`
2. 314场：alpha=`0.9999999700466811`，LL gain=`0.00255106875386224`
3. 314场：alpha=`3.737685985848599e-09`，LL gain≈`-6.58e-12`

结论必须准确表述：**全943场有非常小的正LL增益，但时间稳定性弱。** stop rule未触发，所以按预注册可以进入一次性fixed target300应用；这不等于R55B模型PASS，更不等于平局问题解决。

## target300隔离状态

截至state54发布：

- target rows used for alpha: 0
- target application performed: false
- target score access during alpha fit: 0
- target tuning: false
- frozen target n: 300
- sample hash: `7f874277290f3c9664425f80e5281c18feddea85ff7306ed8ae64e6f6d949ffb`

## 唯一下一步

先完成state54 GitHub + Airtable双边回读一致性核验。只有 `HANDOFF_STATE_SYNC_VERIFIED` 后，才允许下一研究原子步骤：

1. 固定使用alpha=`0.27198933241466033`，不得重拟合；
2. 一次性应用原fixed target300；
3. 按R55同口径计算Direct-T / HDA / Draw指标与bootstrap；
4. target结果可见后禁止任何alpha搜索或修改；
5. 新科学PASS/FAIL/STOP产生后先发布下一state_version。

## 禁止重复/越界

- 不重做943场输入恢复；
- 不重复环境矩阵probe；
- 不重新拟合/搜索alpha；
- 不用target300选择数值环境或alpha；
- 不重新抽300或改sample hash；
- 不per-league alpha / threshold / forced draw / Top-k / class weight；
- 不打开2025/26 confirmation；
- 不调用付费Provider；
- 不修改正式模型、数据、config或CURRENT；
- 不将state54表述成模型PASS。

## 关键锚点

- unique CURRENT: V5.2.0 / count=1
- state_version: 54
- state54 creation log: `recSphqybYbluBFEN`
- current_state: `recs1pQ1rhuwJQAzE`
- execution_checkpoint: `recIRxK7EIMjJdG4A`
- research branch: `research/r55-ou-matched300-20260814`
- research HEAD: `1ef58235de14071387d6e6382237b51f72c0cd6c`
- R55B prereg blob: `ed365d820788d13c36813d114e29631e27bf1998`
- alpha run/job/artifact: `31898043150 / 95044288985 / 9250346172`

## 正式资产变化

- model: 0
- data: 0
- config: 0
- CURRENT: 0
- formal_weight: 0

## 新对话恢复

新对话收到“继续/开始”时先走 `AGENTS.md` FAST_BOOTSTRAP。若state54双边同步已经验证，直接从“冻结alpha一次性应用fixed target300”继续；不得退回输入恢复、环境probe或重新拟合alpha。
