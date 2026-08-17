# 足球项目最后接力点

> 最近语境只看上一工作对话末尾；真正恢复点以 Airtable《当前状态》+唯一《执行检查点》+《会话接力》为准。禁止扫描整个旧项目聊天。

## 接力身份

- project_id: `football-project`
- handoff_version: **32**
- state_version: **61**
- checkpoint_version: **128**
- updated_at_local: `2026-08-17 16:08 +08:00`
- status: `WAITING`
- state_log_record_id: `rec8DSgydfzYix35I`

## 最新完成：B01-B03 底座解剖

研究结论：**`POSTVIEW_BASE_ANATOMY_COMPLETE_NO_PROMOTION`**。

真实研究锚点：

- branch: `research/b01-b03-base-anatomy-r1-20260817`
- HEAD: `cb0b9057bdb7c7cb7ec73b1aa14076231d58ff0f`
- PR: `#199` Draft / Open / Unmerged
- report: `football-data/research/base_anatomy_20260817/B01_B03_OU25_DIRECT_T_BASE_ANATOMY_R1.md`
- scope: B01/B02/B03 = 1200 场；B04 analyzed rows = 0

## 核心事实

### 1. 日期簇不是 400 场不稳定的原因

B01/B02/B03 date-cluster design effect 约 `0.934 / 0.944 / 0.983`，effective N 没有坍塌。

真正瓶颈是净 effect 很小，标准化 `|effect|≈0.050 / 0.095 / 0.058`。

### 2. OU2.5 的收益来自上尾修正

B01-B03 合并：

- 0-3 球：799 场，mean ΔLL=`+0.013766`，candidate 更差；
- 4+ 球：401 场，mean ΔLL=`-0.053242`，candidate 明显更好；
- 三个 batch 方向一致。

因此单一 OU2.5 更像 high-total / upper-tail correction；整体小收益是 0-3 损失与 4+ 收益相消后的残差。

### 3. pooled 1200 仅作描述

- pooled ΔLL=`-0.008626`
- date-block bootstrap90=`[-0.014553,-0.002656]`

全部样本已 VIEWED，禁止把该结果事后升级为 confirmatory PASS。

### 4. 400 场功效太低

按当前观察 effect/variance：

- 400 场 power≈40%
- 80% power≈1257 场
- 90%≈1741 场
- 95%≈2200 场

所以 B02 PASS 而 B01/B03 CI 跨0与低功效设计相容。

### 5. 异步与联赛只留作假设

- `<=1min` 时间差：ΔLL≈`+0.00176`
- `>6h`：ΔLL≈`-0.01750`
- 但 quote age 与收益近零相关，控制 realized 4+ 后异步项不再显著；不能写成“旧报价更好”。
- PL/Liga 描述性较好，Ligue 1 三包较差；competition joint robust test `p≈0.402`，不授权 selector。

## 永久治理继续有效

`gold-standard / 金标准 reserve` 已永久退役：不新建、不恢复、不改名复活；B04 不承担金标准角色。

## 当前唯一下一步

冻结本轮 post-view 解剖，**不在 B01-B03 上继续调参**。

如果用户继续推进 OU/total-shape 主线，只允许先做新的非金标准预注册 OOS/forward **数据合同与可行性设计**，目标是验证更丰富 total-shape 信息能否减少“0-3 变差 vs 4+ 变好”的相消。

但旧的公开免密钥 multi-OU 批量 PIT 路线已经 `STOP_DATA/COVERAGE`，禁止原路重复。必须有 genuinely new 的合规同步/PIT 数据条件才能继续；否则保持 WAITING。

## 禁止重复/越界

- 不把 pooled 1200 写成 confirmatory PASS；
- 不按联赛、时间差、4+真实标签后验造 selector；
- 不重新把 B01-B03 当 holdout；
- 不用 B04 作为本次分析证据；
- 不恢复 gold-standard reserve；
- 不重复已 STOP 的公开 multi-OU 路线；
- 不修改 formal model/data/config/CURRENT；
- 不恢复 Direct-T / Parity 外壳搜索。

## 新聊天收到“继续”时

恢复：上一工作对话末尾小段（若可得）→ Airtable 当前状态 state61 → checkpoint `recIRxK7EIMjJdG4A` v128 → 会话接力 → 核验 GitHub main。然后只从上面的唯一下一步继续。
