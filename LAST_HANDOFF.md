# 足球项目最后接力点

> 恢复只看上一工作对话末尾 + Airtable 当前状态/检查点/会话接力 + GitHub main。旧层若写 B04 未开封，必须先应用 state62 stale 规则。

## 接力身份

- project_id: `football-project`
- handoff_version: **33**
- state_version: **62**
- checkpoint_version: **129**
- updated_at_local: `2026-08-17 16:28 +08:00`
- status: `WAITING`
- state_log_record_id: `reciwMYK9dJCYZB60`

## 最新完成：B04 VIEWED 状态漂移治理闭环

裁决：**`B04_VIEWED_STATE_DRIFT_CLOSED`**

永久恢复真值：

**`B04 = VIEWED since settlement run 31998704387; all four batches closed; no unopened reserve remains.`**

治理文件：`governance/B04_VIEWED_STATE_DRIFT_CLOSURE.md`

## 精确历史

### 旧 checkpoint125

- 约 `2026-08-17 13:24 +08:00`
- 当时 B03 已结算，B04 自身 settlement 尚未发生
- 所以当时“B04 未被 target-only query 请求”是历史时点事实
- 但后续 GitHub 分支继续执行而未回写 Airtable

### B04 freeze

- commit `23291a89d874446a3306a5e37b2a1ee83fe84b8c`
- `2026-08-17 13:37:33 +08:00`
- run `31998560168`
- Artifact `9277646065`
- ZIP SHA-256 `6ea9db8397e07087731a02c11c084196ab8b2e5356d43e7be0b593405e3ea221`
- 359 rows
- `PREDICTIONS_FROZEN_LABELS_UNOPENED`

### B04 settlement：正式变成 VIEWED

- commit `93cde4380d8b8d4705c04d811b0bdf2ef978092d`
- `2026-08-17 13:40:04 +08:00`
- run `31998704387`
- Artifact `9277727825`
- ZIP SHA-256 `3e012815386508a4b0e363fcc10099b3af7550128e737533eae18b7fffeb9d4b`
- target-only receipt SHA-256 `45d3df41d516da08a1f571a937d4bbf7f6770136094f6962e762bb9e7d458116`
- `B04_labels_opened_in_this_run=359`
- `non_B04_fixture_ids_requested=0`
- B01/B02/B03 labels read in this run = 0
- result `FAIL_B04_OU25_NO_CONFIRMED_DIRECT_T_INCREMENT`
- ΔLL `-0.009181875234092318`
- bootstrap90 `[-0.020674795951751247,+0.002643013130652647]`

### 四包 closure

- commit `54e1c05262f42c731975710c77eb0a0e4dd47a30`
- `2026-08-17 13:46:08 +08:00`
- `all_batches_viewed=true`
- `unopened_batches_remaining=0`
- B01/B02/B03/B04 = FAIL / PASS / FAIL / FAIL

后续 `2813b16d2e17f87f4f8e7618e6589dfe85a1a7d8` 再次确认全部 1559 labels 已 VIEWED。

## stale 规则

自 B04 settlement run `31998704387` 完成以后：

- 任何“B04 未开封 / 可作为下一独立包 / 仍是 blind reserve”的旧文本 = **`STALE_AFTER_B04_SETTLEMENT`**；
- 旧文本可保留为历史，但不得恢复为 ACTIVE_NEXT；
- B04 永久不得再当 holdout / blind reserve / gold-standard reserve；
- B04 target-only settlement 合规不意味着 VIEWED 后还能再次确认。

## state60 / state61 继续有效

- gold-standard reserve 永久退役；
- state61 B01-B03 底座解剖仍为 `POSTVIEW_BASE_ANATOMY_COMPLETE_NO_PROMOTION`；
- research HEAD `cb0b9057bdb7c7cb7ec73b1aa14076231d58ff0f`；PR #199 Draft/Open/Unmerged；
- state61 的 `B04 analyzed rows=0` 只表示该分析没有使用 B04，不表示 B04 未 VIEWED。

## 当前唯一下一步

保持 WAITING，不重新消费已 VIEWED B01-B04。

若用户继续 OU/total-shape 主线，只先设计新的非金标准预注册 OOS/forward 数据合同，并核验 genuinely new、合规、同步/PIT total-shape 数据可行性。旧公开免密钥 multi-OU 路线已 STOP_DATA/COVERAGE，不得原路重复。

## 禁止重复/越界

- 不把任何旧 B04 未开封表述作为当前真值；
- 不再消费 B04 作为独立 holdout；
- 不恢复 gold-standard；
- 不将四包 post-view 聚合改写为 confirmatory PASS；
- 不修改 formal model/data/config/CURRENT。

## 新聊天收到“继续”时

恢复：上一工作对话末尾小段 → Airtable state62 → checkpoint `recIRxK7EIMjJdG4A` v129 → handoff v33 → 核验 GitHub main。若旧信息声称 B04 未开封，直接标记 `STALE_AFTER_B04_SETTLEMENT`，不要再沿旧分叉执行。
