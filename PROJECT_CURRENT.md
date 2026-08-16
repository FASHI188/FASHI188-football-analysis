# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 55
- updated_at_utc: 2026-08-16T03:29:00Z
- updated_by: GPT-5.6 Sol
- status_source: `STATE55_R55B_FIXED_TARGET300_MIXED_NO_PROMOTION`
- status: WAITING
- state_log_record_id: `recncQm7qWc0E5o6D`

## 正式规则状态

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0
- formal model/data/config/CURRENT changes in state55: 0
- R55/R55B remain research-only; formal_weight=0

## state55 结论

R55B 已按预注册约束完成 frozen target300 的一次性应用。结果为**混合、不得晋级**，不能表述为模型 PASS，更不能表述为平局问题已解决。

- research branch: `research/r55-ou-matched300-20260814`
- result commit: `d07bd55ec818dda4efe1b627e77871f658d82357`
- result: `football-data/research/r55b_fixed_target300_result_20260816.json`
- ruling: `R55B_PARTIAL_OU_MIXED_TARGET_RESULT_REQUIRES_PREREG_INTERPRETATION`
- formal_weight: 0

## 冻结设计与样本身份

- frozen global alpha: `0.27198933241466033`
- alpha refit after target visibility: false
- alpha search on target: false
- target n: 300
- target sample hash: `7f874277290f3c9664425f80e5281c18feddea85ff7306ed8ae64e6f6d949ffb`
- target resampled: false
- sample hash changed: false
- target window: `2016-03-01 20:45:00` to `2016-04-16 16:00:00`
- actual H/D/A: 120 / 88 / 92
- actual draws: 88
- 2025/26 confirmation access: 0

## 成功执行证据

- successful workflow run: `31924215428`
- job: `95109102754`
- Artifact: `9257327236`
- Artifact digest: `sha256:05b29bfa7cfe6be08ead1e4cf7bf411d4c14505048625fc2258bfee1fdd11ff1`
- result JSON SHA-256: `13798b7c5a1c365378fe7c061254bcf7d402b9cd81ce1864c5034be3c00a8684`
- runtime: Python 3.13.15 / NumPy 2.3.5 / SciPy 1.17.0 / scikit-learn 1.8.0

成功 run 全步骤通过，包括原 frozen target300 应用和唯一结果 Artifact 上传。

## R55B 指标

### Direct-T

Baseline:
- LogLoss = `1.7969674347148699`
- RPS = `0.11963024950838064`
- Top-1 accuracy = `0.23666666666666666`

Partial OU:
- LogLoss = `1.7967403733078462`
- RPS = `0.1195555944280619`
- Top-1 accuracy = `0.23333333333333334`

Delta partial - baseline:
- LogLoss = `-0.0002270614070236654`（微幅改善）
- RPS = `-0.0000746550803187307`（微幅改善）
- Top-1 accuracy = `-0.3333333333333327 pp`（变差）

### HDA

Baseline:
- LogLoss = `1.0478911519820855`
- Brier = `0.631361792403141`
- accuracy = `0.44666666666666666`
- draw_n = `0`

Partial OU:
- LogLoss = `1.048022940199128`
- Brier = `0.6314631255891882`
- accuracy = `0.44666666666666666`
- draw_n = `0`

Delta partial - baseline:
- LogLoss = `+0.00013178821704240562`（变差）
- Brier = `+0.0001013331860471034`（变差）
- accuracy = `0 pp`

### Draw

Baseline:
- LogLoss = `0.6049476557427765`
- Brier = `0.20737667470234286`
- AUC = `0.5073434819897085`

Partial OU:
- LogLoss = `0.6050794439598187`
- Brier = `0.20743239883457343`
- AUC = `0.504127358490566`

Delta partial - baseline:
- LogLoss = `+0.00013178821704218358`（变差）
- Brier = `+0.000055724132230566825`（变差）
- AUC = `-0.0032161234991424648`（变差）

## Bootstrap 解释

90% bootstrap 不能支持稳定增益：

- Direct-T LL delta: p05=`-0.0019769387995008957`, median=`-0.00026376905838918017`, p95=`+0.0014920857999691506`, P(delta<0)=`0.5985`
- HDA LL delta: p05=`-0.00038915851250222346`, median=`+0.00013266531136338393`, p95=`+0.0006466047395584438`, P(delta<0)=`0.336`
- Draw LL delta: P(delta<0)=`0.336`

因此 Direct-T 的微弱改善不稳定，且没有传递为 HDA/Draw 改善。

## 科学解释

R55 已证明独立 OU 在 T>=3 分组上存在一定正交信息，但 full hard projection 无稳定增益。R55B 用 pre-target 数据冻结部分 alpha 后，在原300上再次看到：

1. OU/T>=3 分组和 Direct-T LogLoss/RPS存在极弱改善；
2. HDA LogLoss/Brier反而变差；
3. Draw LogLoss/Brier/AUC反而变差；
4. HDA Top-1 平局预测仍为 0；
5. bootstrap 区间跨 0，没有稳定证据。

结论：**OU残差信号目前不能作为平局问题的可用解法，也没有达到 clean increment 晋级条件。** 不允许继续用已经打开的 target300 搜索 alpha、阈值或其他目标驱动参数。

## 技术可复现性裁决

R55B 的最终科学结论没有通过放宽科学标准得到。执行中发现两类数值环境复现问题，并单独做了 research-only 技术裁决：

- `r55_baseline_iteration_fingerprint_resolution_20260816.json`：精确 solver `n_iter_` 是数值环境敏感元数据，不是模型身份指纹；身份门仍要求精确训练数据、特征、模型规格、class order 与收敛。
- `r55b_endpoint_reproduction_gate_resolution_20260816.json`：连续旧 endpoint 使用绝对容差 `1e-6`；AUC 因排序统计量不连续，仅允许最多 1 个 positive-negative pair 的排序漂移，且始终报告实际计算 AUC，不替换为历史值。

这些裁决不改变 alpha、样本、模型规格、指标定义、最终 ruling 或正式权重。

## 当前禁止事项

- 不重新拟合或搜索 R55B alpha；
- 不根据 target300 结果修改 alpha；
- 不重新抽300或改 sample hash；
- 不做 per-league alpha；
- 不做 threshold search / forced draw / Top-k / class weight；
- 不把 R55B 写成模型 PASS 或平局问题解决；
- 不据此正式晋级；
- 不打开2025/26 confirmation；
- 不调用付费 Provider；
- 不修改正式模型、正式数据、正式 config 或 CURRENT。

## 唯一下一步

R55B 研究原子步骤关闭。下一步只做**足球仓库减负治理的只读审计**：

1. 精确列出当前活跃入口与依赖；
2. 对历史 workflow / research assets / governance docs / branches / PR 做保留、归档、删除候选分类；
3. 先只读，不立即删除；
4. 默认禁止递归整仓库 tree scan；已知 path/HEAD/run/Artifact 时必须精确读取；
5. 治理不得改变正式模型、数据、config、CURRENT或任何科学结论。

在仓库治理审计形成明确方案前，不启动新的 R55B target 驱动实验。

## Airtable锚点

- base: `足球项目接续`
- current_state_record: `recs1pQ1rhuwJQAzE`
- state55 creation log: `recncQm7qWc0E5o6D`
- execution_checkpoint_record: `recIRxK7EIMjJdG4A`

## 权威优先级

当前用户明确指令 > 唯一正式 CURRENT > ACTIVE_CHECKPOINT实时协议与唯一激活检查点 > LAST_HANDOFF > PROJECT_CURRENT + Airtable当前状态/绑定维护日志 > 历史聊天/旧文件/记忆。
