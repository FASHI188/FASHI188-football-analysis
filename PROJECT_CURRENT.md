# 足球项目当前状态

## 身份

- project_id: `football-project`
- state_version: **61**
- updated_at_local: `2026-08-17 16:08 +08:00`
- updated_by: `GPT-5.6 Sol`
- status_source: `POSTVIEW_BASE_ANATOMY_COMPLETE_NO_PROMOTION`
- status: `WAITING`
- state_log_record_id: `rec8DSgydfzYix35I`
- predecessor_state: 60

## 正式规则与正式资产

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- formal model/data/config/CURRENT changes in state61: **0**
- formal_weight changes in state61: **0**

state61 只发布 B01-B03 已 VIEWED 样本的 post-view 底座解剖结论，不新增确认门、不训练、不调参、不晋级模型。

## state60 永久治理仍有效

`gold-standard / 金标准 reserve` 路线继续为 **永久退役**：

- 不得新建、恢复或改名复活；
- B04 不得包装成金标准或最高等级确认包；
- 旧日志/旧聊天里的“B04或新gold-standard二选一”不得恢复为 ACTIVE_NEXT。

永久治理文件：`governance/GOLD_STANDARD_RESERVE_RETIRED.md`。

## state61 新研究结论：B01-B03 底座解剖完成

研究锚点：

- branch: `research/b01-b03-base-anatomy-r1-20260817`
- HEAD: `cb0b9057bdb7c7cb7ec73b1aa14076231d58ff0f`
- PR: **#199 Draft / Open / Unmerged**
- report: `football-data/research/base_anatomy_20260817/B01_B03_OU25_DIRECT_T_BASE_ANATOMY_R1.md`
- scope: B01/B02/B03 共 **1200** 场；B04 analyzed rows = **0**
- verdict: **`POSTVIEW_BASE_ANATOMY_COMPLETE_NO_PROMOTION`**

### 1. 日期簇不是 400 场不稳定的主因

B01/B02/B03 的 calendar-date cluster design effect 分别约：

- B01: `0.934`
- B02: `0.944`
- B03: `0.983`

对应 effective N 约 `428 / 424 / 407`，没有因日期簇相关性发生样本量坍塌。

真正瓶颈是净效应相对逐场波动太小：三个 batch 的标准化 `|effect|` 仅约 `0.050 / 0.095 / 0.058`。

### 2. 核心机制：0-3 球损失与 4+ 球收益相消

B01-B03 合计：

- 实际总进球 0-3：`n=799`，mean ΔLogLoss=`+0.013766`，candidate 更差；
- 实际总进球 4+：`n=401`，mean ΔLogLoss=`-0.053242`，candidate 明显更好；
- 三个 batch 均保持这个方向。

因此单一 OU2.5 cut point 更像 **high-total / upper-tail correction**，并非对整个 Direct-T 分布均匀改善。整体小幅净收益是两股更大的反向分量相互抵消后的残差。

### 3. 1200 场 pooled 只允许描述，不是确认门

B01-B03 post-view pooled：

- ΔLogLoss=`-0.008626`
- date-block bootstrap90=`[-0.014553, -0.002656]`

因为三包均已 VIEWED，**禁止**把 pooled CI 低于 0 事后改写成 confirmatory PASS，也不得据此发明新的组合门。

### 4. 400 场设计本身功效不足

以当前观察到的 pooled effect / cluster variance 作为纯描述性假设：

- 400 场：power 约 `40%`
- 800 场：约 `63%`
- 1200 场：约 `78%`
- 80% power：约 `1257` 场
- 90% power：约 `1741` 场
- 95% power：约 `2200` 场

所以 B02 PASS、B01/B03 CI 跨 0 并不要求假设底层效应互相矛盾；400 场对于这种小净效应本身就是低功效设计。

### 5. 异步报价不是“旧报价更好”的证据

B01-B03 描述性分组：

- HDA/OU 时间差 `<=1min`：n=566，ΔLL≈`+0.00176`
- 时间差 `>6h`：n=586，ΔLL≈`-0.01750`

但 quote age 与 ΔLL 的 Pearson/Spearman 相关几乎为 0；控制 batch、competition、baseline/shift 后异步项仍偏负，但再控制 realized 4+ outcome 后不再显著。

裁决：这是 **timing/source/data-generating-process regime association**，不能解释为 stale quote 具有因果优势。

### 6. 联赛差异只保留为假设

描述上：Premier League、Liga 较好；Ligue 1 三包均为正向 ΔLL（即 candidate 更差）。但 cluster-robust competition joint test `p≈0.402`，不足以授权联赛 selector。

禁止按联赛、时间差或实际 4+ 标签在已 VIEWED 数据上后验切段造规则。

## 数据锚点

- post-view source run: `32000375197`
- post-view Artifact: `9278178012`
- source ZIP SHA-256: `4af1f68c4d7769ffc957fbd93d1d9c0102a4e4275ab41842e027686d601d6f73`
- B01 freeze: run `31993949268`, Artifact `9276244641`
- B02 freeze: run `31995652017`, Artifact `9276762428`
- B03 freeze: run `31997077090`, Artifact `9277190607`

## 唯一下一步

**冻结本轮底座解剖，不在 B01-B03 上继续调参。**

若继续研究 OU / total-shape 信息，只允许先设计一个新的、明确的 **非金标准** 预注册 OOS/forward 数据合同，核心问题是：

> 更丰富的赛前 total-shape 信息能否减少“0-3 球变差 vs 4+ 球变好”的相消，而不是只用 OU2.5 单一 cut point 移动总进球强度？

但当前公开免密钥 multi-OU 批量 PIT coverage 路线此前已裁决 `STOP_DATA/COVERAGE`，因此**不得原路重复探测**。下一步若要推进，先找 genuinely new、合规、可同步/PIT 的 total-shape 数据可行性；没有新数据条件就保持 WAITING，而不是继续模型搜索。

## 当前禁止事项

- 禁止把 pooled 1200 结果写成 confirmatory PASS；
- 禁止基于 B01-B03 的联赛、时间差、实际 4+ 标签后验造 selector；
- 禁止重新把 B01/B02/B03 当独立 holdout；
- 禁止消费/分析 B04 作为本结论证据；
- 禁止恢复 gold-standard / 金标准 reserve；
- 禁止重复已 STOP 的公开免密钥 multi-OU 路线；
- 禁止修改 formal model/data/config/CURRENT；
- 禁止恢复 Direct-T / Parity 外壳搜索。

## 轻量接续

新聊天恢复：上一工作对话末尾小段（若可得）→ Airtable《当前状态》→唯一《执行检查点》→《会话接力》→核验 GitHub main。旧层若与 state61 冲突，以 state61 为准。

## Airtable 锚点

- base: `足球项目接续` (`appLXF9IBvSCEUjJV`)
- current_state_record: `recs1pQ1rhuwJQAzE`
- state61 creation log: `rec8DSgydfzYix35I`
- execution_checkpoint_record: `recIRxK7EIMjJdG4A`
- checkpoint_version: **128**

## 权威优先级

当前用户明确指令 > 唯一正式 CURRENT > 最新 Airtable state/checkpoint/handoff > GitHub 当前状态文件 > 历史聊天/旧文件/记忆。
