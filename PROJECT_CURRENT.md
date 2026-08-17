# 足球项目当前状态

## 身份

- project_id: `football-project`
- state_version: **62**
- updated_at_local: `2026-08-17 16:28 +08:00`
- updated_by: `GPT-5.6 Sol`
- status_source: `B04_VIEWED_STATE_DRIFT_CLOSED`
- status: `WAITING`
- state_log_record_id: `reciwMYK9dJCYZB60`
- predecessor_state: 61

## 正式规则与正式资产

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- formal model/data/config/CURRENT changes in state62: **0**
- formal_weight changes in state62: **0**

state62 只闭合 B04 历史 VIEWED 状态漂移和恢复语义，不改任何科学结果、模型、数据、配置或正式 CURRENT。

## state62 治理结论：B04 历史状态漂移已闭环

治理文件：`governance/B04_VIEWED_STATE_DRIFT_CLOSURE.md`

最终裁决：

**`B04 = VIEWED since settlement run 31998704387; all four batches closed; no unopened reserve remains.`**

### 旧 Airtable checkpoint125 为什么写 B04 未开封

旧 state59 / checkpoint125 更新时间约 `2026-08-17 13:24 +08:00`。该时点 B03 已结算，但 B04 自身 freeze/settlement 尚未发生，所以当时“B04 未被 target-only query 请求”的记录在历史时点上成立。

之后 GitHub 研究分支继续执行，但没有及时回写 Airtable，形成状态漂移。

因此旧 checkpoint125 不是删除或改写，而是从 B04 settlement 完成以后统一标记：

**`STALE_AFTER_B04_SETTLEMENT`**

只可用于历史还原，不得作为当前 ACTIVE_NEXT、盲态证据或恢复真值。

## B04 精确状态转移

### 1. B04 零标签冻结

- freeze commit: `23291a89d874446a3306a5e37b2a1ee83fe84b8c`
- local time: `2026-08-17 13:37:33 +08:00`
- freeze run: `31998560168`
- freeze Artifact: `9277646065`
- Artifact ZIP SHA-256: `6ea9db8397e07087731a02c11c084196ab8b2e5356d43e7be0b593405e3ea221`
- rows: `359`
- lock state: `PREDICTIONS_FROZEN_LABELS_UNOPENED`
- target labels accessed: `0`
- target outcome columns read: `[]`

### 2. B04 正式 settlement：UNOPENED → VIEWED

- settlement commit: `93cde4380d8b8d4705c04d811b0bdf2ef978092d`
- local time: `2026-08-17 13:40:04 +08:00`
- settlement run: `31998704387`
- settlement Artifact: `9277727825`
- Artifact ZIP SHA-256: `3e012815386508a4b0e363fcc10099b3af7550128e737533eae18b7fffeb9d4b`
- target-only label receipt SHA-256: `45d3df41d516da08a1f571a937d4bbf7f6770136094f6962e762bb9e7d458116`
- settlement summary SHA-256: `1977f7b78be0023c4b29637392e2becbeea73fbf9b8b251b47e08f784cc837e1`

该 run 明确：

- `B04_labels_opened_in_this_run=359`
- `non_B04_fixture_ids_requested=0`
- `label_columns_materialized_for_non_B04_rows_in_this_run=0`
- B01/B02/B03 labels read in this run = `0`

从该 run 完成起，B04 永久为 `VIEWED`。

B04 原锁定合同结果：

- status: `FAIL_B04_OU25_NO_CONFIRMED_DIRECT_T_INCREMENT`
- ΔLogLoss: `-0.009181875234092318`
- ΔBrier: `-0.003064149231567755`
- ΔRPS: `-0.001680583952775756`
- bootstrap90: `[-0.020674795951751247, +0.002643013130652647]`
- primary gate: FAIL
- formal_weight: `0`

### 3. 四包 reserve 完全关闭

- closure commit: `54e1c05262f42c731975710c77eb0a0e4dd47a30`
- local time: `2026-08-17 13:46:08 +08:00`
- status: `CLOSED_NO_CONFIRMATORY_PROMOTION`
- `all_batches_viewed=true`
- `unopened_batches_remaining=0`

B01/B02/B03/B04 单包结果：FAIL / PASS / FAIL / FAIL。四包 ΔLogLoss 点估计均为负，但只有 B02 通过原锁定单包确认门。

后续 post-view closure `2813b16d2e17f87f4f8e7618e6589dfe85a1a7d8` 再次明确 `all_target_labels_already_viewed=true`，scope=1559。

## 与 state60 / state61 的关系

### state60 继续有效

`gold-standard / 金标准 reserve` 永久退役，不得新建、恢复、改名复活；B04 尤其不得重新包装成任何 blind/gold-standard reserve。

### state61 继续有效

B01-B03 底座解剖结论保持不变：

- research HEAD: `cb0b9057bdb7c7cb7ec73b1aa14076231d58ff0f`
- PR #199: Draft / Open / Unmerged
- verdict: `POSTVIEW_BASE_ANATOMY_COMPLETE_NO_PROMOTION`
- B04 analyzed rows in that analysis: `0`

这里的 `B04 analyzed rows=0` 是分析 scope 主动排除 B04，不代表当时 B04 未 VIEWED。

## 当前唯一下一步

保持 state61 的科学断点，不重新消费已 VIEWED reserve：

若用户继续 OU/total-shape 主线，只允许先设计新的非金标准预注册 OOS/forward 数据合同，并先核验是否存在 genuinely new、合规、同步/PIT 的 total-shape 数据条件。

旧公开免密钥 multi-OU 路线已 `STOP_DATA/COVERAGE`，不得原路重复；若没有新数据条件，保持 `WAITING`。

## 当前禁止事项

- 禁止把任何旧“B04 未开封”文本作为当前恢复真值；
- 禁止再次把 B04 当 holdout / blind reserve / gold-standard reserve；
- 禁止把 B04 合规 target-only settlement 曲解为它仍可重新用于确认；
- 禁止把四包 post-view 聚合结果改写成新的 confirmatory PASS；
- 禁止恢复 gold-standard reserve；
- 禁止把 B01-B03/B04 重新当未看标签样本；
- 禁止重复已 STOP 的公开 multi-OU 数据路线；
- 禁止修改 formal model/data/config/CURRENT。

## 轻量接续

新聊天恢复：上一工作对话末尾小段（若可得）→ Airtable《当前状态》→唯一《执行检查点》→《会话接力》→核验 GitHub main。

只要旧层出现“B04 still unopened / 可作为下一独立包”，直接应用 `STALE_AFTER_B04_SETTLEMENT`，不得继续该旧分叉。

## Airtable 锚点

- base: `足球项目接续` (`appLXF9IBvSCEUjJV`)
- current_state_record: `recs1pQ1rhuwJQAzE`
- state62 creation log: `reciwMYK9dJCYZB60`
- execution_checkpoint_record: `recIRxK7EIMjJdG4A`
- checkpoint_version: **129**
- session_handoff_version: **12**

## 权威优先级

当前用户明确指令 > 唯一正式 CURRENT > 最新 Airtable state/checkpoint/handoff > GitHub 当前状态文件 > 历史聊天/旧文件/记忆。
