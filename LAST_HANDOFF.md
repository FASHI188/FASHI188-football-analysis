# 足球项目最后对话接力点

> 实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；长期启动/长任务规则读取 `AGENTS.md`；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 26
- state_version: 55
- updated_at_utc: 2026-08-16T03:30:00Z
- status: WAITING
- state_log_record_id: `recncQm7qWc0E5o6D`

## 用户当前授权

用户已授权先完成 R55B，再进行足球仓库减负治理。R55B 已完成并关闭；下一步只允许仓库治理**只读审计**，先列保留/归档/删除候选，不直接大删。

## R55B 已完成

- research branch: `research/r55-ou-matched300-20260814`
- current research/result HEAD: `d07bd55ec818dda4efe1b627e77871f658d82357`
- result file: `football-data/research/r55b_fixed_target300_result_20260816.json`
- frozen alpha: `0.27198933241466033`
- fixed target n: 300
- sample hash: `7f874277290f3c9664425f80e5281c18feddea85ff7306ed8ae64e6f6d949ffb`
- target resampled: false
- alpha refit/search after target: false
- formal_weight: 0

Successful execution:
- run: `31924215428`
- job: `95109102754`
- Artifact: `9257327236`
- Artifact digest: `sha256:05b29bfa7cfe6be08ead1e4cf7bf411d4c14505048625fc2258bfee1fdd11ff1`
- result JSON SHA-256: `13798b7c5a1c365378fe7c061254bcf7d402b9cd81ce1864c5034be3c00a8684`

## 真实科学结论

Ruling:
`R55B_PARTIAL_OU_MIXED_TARGET_RESULT_REQUIRES_PREREG_INTERPRETATION`

关键结果：

- Direct-T LogLoss delta partial-baseline = `-0.0002270614070236654`：微幅改善；
- Direct-T RPS delta = `-0.0000746550803187307`：微幅改善；
- Direct-T Top-1 accuracy = `-0.3333333333333327 pp`：变差；
- HDA LogLoss delta = `+0.00013178821704240562`：变差；
- HDA Brier delta = `+0.0001013331860471034`：变差；
- Draw LogLoss delta = `+0.00013178821704218358`：变差；
- Draw Brier delta = `+0.000055724132230566825`：变差；
- Draw AUC delta = `-0.0032161234991424648`：变差；
- HDA draw_n = `0`，仍没有 Top-1 平局预测。

Bootstrap：
- Direct-T P(delta<0)=`0.5985`，90%区间跨0；
- HDA P(delta<0)=`0.336`；
- Draw P(delta<0)=`0.336`。

结论必须表述为：**R55B 在 Direct-T/OU 分组上只有极弱、不稳定的改善，但没有转化为 HDA/Draw 改善；平局问题仍未解决，R55B 不得晋级。**

## 执行中技术复现问题

以下失败属于技术门，不属于新的科学实验：

1. run `31923136286`：alpha audit字段读取错误，未进入 target；
2. run `31923236804`：已读取原300，在旧 R55 连续 endpoint 的过严 `2e-9` 复现门失败；
3. run `31924132200`：连续 endpoint 在 `1e-6` 内均通过，只剩 `draw_hard_auc` 相差恰好一个 positive-negative pair 排序量子；
4. run `31924215428`：采用预先落盘的 portable reproduction gate 后成功。

技术裁决文件：
- `football-data/research/r55_baseline_iteration_fingerprint_resolution_20260816.json`
- `football-data/research/r55b_endpoint_reproduction_gate_resolution_20260816.json`

边界：这些裁决不改 alpha、样本、模型规格、指标定义、ruling 或正式权重；AUC始终报告实际计算值。

## 正式资产变化

- model: 0
- data: 0
- config: 0
- CURRENT: 0
- formal_weight: 0

unique CURRENT 仍为 V5.2.0 / count=1。

## 禁止重复/越界

- 不重新拟合或搜索 R55B alpha；
- 不根据已经打开的 target300 结果修改 alpha；
- 不重抽300、不改 sample hash；
- 不做 per-league alpha / threshold / forced draw / Top-k / class weight；
- 不重新跑 R55B 来挑有利数值环境；
- 不将 R55B 表述为模型 PASS 或平局问题解决；
- 不正式晋级；
- 不打开2025/26 confirmation；
- 不调用付费 Provider；
- 不修改正式模型、数据、config或CURRENT。

## 唯一下一步

启动**足球仓库减负治理只读审计**，不是继续 R55B：

1. 只点读明确入口和目录，不做 recursive full-tree scan；
2. 识别当前真正活跃的 workflow / 状态文件 / research 入口；
3. 对历史 workflow、研究资产、治理文档、branches、PR 分类为“保留 / 归档 / 删除候选”；
4. 先出方案，不立即执行破坏性删除；
5. 治理本身不得改变科学结论或正式资产。

## 关键锚点

- state_version: 55
- state55 creation log: `recncQm7qWc0E5o6D`
- current_state: `recs1pQ1rhuwJQAzE`
- execution_checkpoint: `recIRxK7EIMjJdG4A`
- research branch: `research/r55-ou-matched300-20260814`
- research result HEAD: `d07bd55ec818dda4efe1b627e77871f658d82357`
- successful run/job/artifact: `31924215428 / 95109102754 / 9257327236`

## 新对话恢复

新对话收到“继续/开始”时走 `AGENTS.md` FAST_BOOTSTRAP。若 state55 双边同步已验证，**不要再恢复 R55B target300**；从“仓库减负治理只读审计”继续。
