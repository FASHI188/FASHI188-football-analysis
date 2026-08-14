# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 53
- updated_at_utc: 2026-08-14T10:14:00Z
- updated_by: GPT-5.6 Sol
- status_source: `STATE53_R55_HARD_OU_PROJECTION_FAIL_RANKING_SIGNAL_PRESENT`
- status: WAITING
- state_log_record_id: `recU6blnhI73XIRWo`

## 正式规则状态

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0
- formal model/data/config/CURRENT changes in state53: 0
- all R55 work remains research-only, formal_weight=0

## 接续治理

state51的“实质科学结论前置发布硬门”继续生效：任何新的PASS/FAIL/STOP/封存、瓶颈变化或唯一下一方向变化产生后，必须先完成新的state_version双边发布，之后才允许下一研究原子步骤。

本state53严格按该门执行：R55固定300结果产生后先发布state53，未直接在同一300上调OU权重。

## R55：独立OU固定300增量试验

### 零标签冻结

- source archive: `archive (1)(1).zip`
- archive SHA-256: `8bb898c3c067dfca4c4e50f7e0fb3f01104b52a1d748c27ea7973ec96b36cab1`
- independent OU: Football-Data 2015/16 `BbAv>2.5 / BbAv<2.5`
- zero-label run: `31790299125`
- zero-label Artifact: `9215167178`
- strict matched: 340 / 400 candidates
- frozen sample: 300
- sample hash: `7f874277290f3c9664425f80e5281c18feddea85ff7306ed8ae64e6f6d949ffb`
- time window: `2016-03-01 20:45:00` to `2016-04-16 16:00:00`
- actual labels opened only after freeze/prereg
- actual H/D/A: 120 / 88 / 92

### 预注册与算法

- prereg commit: `aeb5d4f615c11a7521059234acf8dc850b4bda81`
- result commit: `ff96f9b7f33d189995d68bcd2c6304cae1c5cc76`
- training: 2005-2015 England Premier/Championship/League One/League Two, n=19,422, target overlap=0
- Direct-T: direct 8-class T=0,1,2,3,4,5,6,7+ logistic model; no class weight
- conditional draw: T0 deterministic; odd T impossible; fixed historical models for T2/T4/T6/T7+
- H:A residual split by target grid71 market H:A ratio
- OU: de-vigged `BbAv>2.5/<2.5`, two-group KL/I-projection preserving within-group Direct-T ratios
- forced draw=false; threshold search=false; Top-k override=false

### 固定300结果

Direct-T baseline:
- LogLoss = 1.79696725
- RPS = 0.11963025
- Top1 = 23.6667%

Direct-T + hard OU projection:
- LogLoss = 1.79789309
- delta = +0.00092584 (worse)
- RPS = 0.11957135
- delta = -0.00005890 (tiny improvement)
- Top1 = 23.3333%

HDA:
- baseline LL = 1.04789111
- OU LL = 1.04854976
- delta = +0.00065866 (worse)
- accuracy = 44.6667% both
- natural Top1 Draw = 0 both

Draw layer:
- baseline LL = 0.60494761; Brier=0.20737666; AUC=0.50734348
- OU LL = 0.60560627; Brier=0.20764474; AUC=0.48986921
- OU minus baseline: LL +0.00065866; Brier +0.00026808; AUC -0.01747427

Bootstrap 90%:
- Direct-T LL delta: [-0.0055223, 0.0072826]
- HDA LL delta: [-0.0012598, 0.0025594]
- both cross zero

### 关键新发现

OU本身并非无信息：在同一300场上，预测 `T>=3` 的排序AUC：
- 1X2-only Direct-T group mass = 0.57250850
- independent OU = 0.58545804

但OU组概率硬替换的二元LogLoss反而略差：0.67911110 vs 0.67818525。

因此科学结论从“是否需要独立OU”进一步收敛为：
**OU存在正交排序信息，但直接100%硬投影的注入强度/校准不合适。**

固定裁决：
`FAIL_R55_HARD_OU_PROJECTION_NO_STABLE_INCREMENT`

不得把OU AUC提升单独写成R55模型PASS。

## 唯一下一研究方向

R55B / 下一原子步骤只能研究：

`pre-target historical OU overlap -> learn fixed OU residual strength/calibration -> freeze strength -> apply to same frozen300 -> Direct-T -> conditional draw -> natural H/D/A`

约束：
1. OU注入强度必须完全由目标300之前的独立OU重叠历史学习；
2. 禁止在这300场搜索权重、阈值或挑最佳强度；
3. 300场只允许一次应用冻结参数并评分；
4. 若前置历史无法支持稳定强度估计，必须STOP而不是人工指定权重。

## 当前禁止事项

- 不在固定300上搜索OU权重；
- 不forced draw / Top-k / class weight / 人工1-1奖励；
- 不把OU排序AUC提升冒充最终HDA增益；
- 不读取新的2025/26确认窗；
- 不调用付费Provider；
- 不修改正式模型、正式数据、正式config或CURRENT；
- 不正式晋级R55。

## Airtable锚点

- base: `足球项目接续`
- current_state_record: `recs1pQ1rhuwJQAzE`
- state53 maintenance_log: `recU6blnhI73XIRWo`
- execution_checkpoint: `recIRxK7EIMjJdG4A`
- checkpoint_version: 30

## 权威优先级

当前用户明确指令 > 唯一正式CURRENT > ACTIVE_CHECKPOINT实时协议与唯一激活检查点 > LAST_HANDOFF > PROJECT_CURRENT + Airtable当前状态/绑定维护日志 > 历史聊天/旧文件/记忆。
