# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 53
- updated_at_utc: 2026-08-14T15:56:00Z
- updated_by: GPT-5.6 Sol
- status_source: `STATE53_R55B_RAW_ARCHIVE_REATTACH_BLOCKER_VERIFIED`
- status: BLOCKED
- state_log_record_id: `recU6blnhI73XIRWo`

## 正式规则状态

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0
- formal model/data/config/CURRENT changes in state53: 0
- all R55/R55B work remains research-only, formal_weight=0

## 接续治理

state51的“实质科学结论前置发布硬门”继续生效：任何新的PASS/FAIL/STOP/封存、瓶颈变化或唯一下一方向变化产生后，必须先完成新的state_version双边发布，之后才允许下一研究原子步骤。

state53的科学结论没有变化。本次只修复同一state53内已经发生的接续漂移：Airtable实时执行检查点已推进到R55B输入恢复穷尽审计，但GitHub本文件仍停留在更早的“dispatch gap”描述。后续实时原子步骤仍以Airtable唯一激活《执行检查点》为准；高频checkpoint_version本身不要求递增state_version。

另确认一项绑定纪律：`state_log_record_id`只绑定建立state53的维护日志`recU6blnhI73XIRWo`，不得被后续普通维护日志覆盖。

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

因此科学结论保持：
**OU存在正交排序信息，但直接100%硬投影的注入强度/校准不合适。**

固定裁决：
`FAIL_R55_HARD_OU_PROJECTION_NO_STABLE_INCREMENT`

不得把OU AUC提升单独写成R55模型PASS。

## R55B 预注册状态

- branch: `research/r55-ou-matched300-20260814`
- exact research HEAD: `2e786c66cfd1678d592bb6e16f666eabf70db611`
- prereg: `football-data/research/r55b_pretarget_ou_alpha_prereg_20260814.json`
- frozen target n: 300
- frozen target sample hash: `7f874277290f3c9664425f80e5281c18feddea85ff7306ed8ae64e6f6d949ffb`
- alpha: single global scalar in [0,1]
- alpha fitting data: only strict pre-target historical OU overlap before 2016-03-01
- target300 may not be used to select alpha
- current execution state: `WAITING_RAW_ARCHIVE_REATTACH`
- R55B run_id: absent
- R55B job_id: absent
- R55B result: absent

## R55B 输入恢复状态

已穷尽当前仓库/Git历史/Actions/File Library可无损恢复路径：

- calibration完整恢复475条；
- calibration-min完整恢复471条；
- 两者均只到2015-11-28；
- R55 fixed target300独立OU逐场输入已从Artifact `9215167178`完整恢复；
- Git历史只确认现存`cal_01/cal_02/calmin_01`分片；
- Actions未发现可替代的R55逐场archive grid71评分/1X2中间Artifact。

当前缺失且不可人工替代：

- 2015-11-28之后至2016-02-29的pre-target archive grid71校准尾段；
- fixed target300对应的archive grid71 `qH/qD/qA`输入。

因此当前不能合法拟合alpha或评分target300；R55B模型计算尚未启动。

## 唯一下一研究方向

保持R55B冻结科学设计和固定300完全不变：

1. 重新附加同一 `archive (1)(1).zip`；
2. 第一动作只校验SHA-256，必须等于 `8bb898c3c067dfca4c4e50f7e0fb3f01104b52a1d748c27ea7973ec96b36cab1`；
3. SHA不匹配立即STOP，不使用近似archive；
4. SHA匹配后，确定性补齐缺失pre-target tail与target300 archive grid71输入；
5. 再落R55B runner/workflow；
6. 真实dispatch并取得concrete run_id/job_id后，才允许执行状态标记RUNNING；
7. 前置校准n<300则按预注册STOP；
8. alpha冻结后仅一次应用原固定300；
9. 任何新的科学PASS/FAIL/STOP产生后，先发布新state_version。

## 当前禁止事项

- 不重新抽300场；
- 不用471条截断校准集直接拟合alpha；
- 不用Football-Data 1X2、近似公式或其他来源替代archive grid71；
- 不在固定300上搜索OU权重；
- 不forced draw / Top-k / class weight / 人工1-1奖励；
- 不把输入审计run称为R55B模型run；
- 不读取新的2025/26确认窗；
- 不调用付费Provider；
- 不修改正式模型、正式数据、正式config或CURRENT；
- 不正式晋级R55/R55B。

## Airtable锚点

- base: `足球项目接续`
- current_state_record: `recs1pQ1rhuwJQAzE`
- state53 creation log: `recU6blnhI73XIRWo`
- latest R55B recovery maintenance log: `rec1LbQyn0bQzXrR3`
- execution_checkpoint_record: `recIRxK7EIMjJdG4A`
- realtime checkpoint_version: 以Airtable唯一激活记录为准，不在本文件内写死

## 权威优先级

当前用户明确指令 > 唯一正式CURRENT > ACTIVE_CHECKPOINT实时协议与唯一激活检查点 > LAST_HANDOFF > PROJECT_CURRENT + Airtable当前状态/绑定维护日志 > 历史聊天/旧文件/记忆。
