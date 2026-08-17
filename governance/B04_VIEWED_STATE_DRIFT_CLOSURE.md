# B04 VIEWED 状态漂移闭环

## 裁决

状态：`B04_VIEWED_STATE_DRIFT_CLOSED`

本文件只做历史事实与恢复语义治理，不修改模型、数据、配置或唯一正式 CURRENT。

## 为什么会出现“B04 未开封”与“B04 已 VIEWED”并存

旧 Airtable state59 / checkpoint125 的更新时间约为 `2026-08-17 13:24 +08:00`。该断点形成时，B03 已完成结算，但 B04 尚未执行它自己的冻结与结算，因此当时写“B04 未被 target-only query 请求、可作为后续 359 场同合同包”在该时点是历史上成立的。

之后研究分支继续向前执行，但这段进展没有及时回写 Airtable，造成恢复时的状态漂移。

因此：

- 旧断点不是伪造事实；
- 但它从 B04 settlement 完成起即成为 `STALE_AFTER_B04_SETTLEMENT`；
- 它不得再作为 ACTIVE_NEXT 或当前盲态证据。

## 精确时间线

### 1. B03 结算完成，旧断点仍有效

- commit: `3d7b1351e7854a44633ddd54ab3e1310983553a2`
- UTC: `2026-08-17T05:19:59Z`
- local: `2026-08-17 13:19:59 +08:00`
- message: `research: settle B03 from immutable freeze artifact`

旧 Airtable checkpoint125 在约 13:24 +08:00 记录 B04 尚未被 B03 target-only query 请求。

### 2. B04 独立零标签冻结

- commit: `23291a89d874446a3306a5e37b2a1ee83fe84b8c`
- UTC: `2026-08-17T05:37:33Z`
- local: `2026-08-17 13:37:33 +08:00`
- message: `research: freeze sealed B04 before labels`
- freeze run: `31998560168`
- freeze artifact: `9277646065`
- freeze artifact ZIP SHA-256: `6ea9db8397e07087731a02c11c084196ab8b2e5356d43e7be0b593405e3ea221`
- B04 rows: `359`

该冻结工作流明确验证：

- B04 manifest 为原 sealed B04；
- `target_labels_accessed=0`；
- `target_outcome_columns_read=[]`；
- 冻结预测 lock 状态为 `PREDICTIONS_FROZEN_LABELS_UNOPENED`；
- 在 B04 自身冻结阶段没有打开 B04 outcome。

这与更早 B01 旧 settlement 进程曾整列物化 fixture outcome 的治理注记并不矛盾：后者是旧进程层技术性物化事实；B04 独立 freeze 本身仍为零 outcome-column 读取。

### 3. B04 真正从 UNOPENED 转为 VIEWED

- settlement commit: `93cde4380d8b8d4705c04d811b0bdf2ef978092d`
- UTC: `2026-08-17T05:40:04Z`
- local: `2026-08-17 13:40:04 +08:00`
- message: `research: settle B04 from immutable freeze artifact`
- settlement run: `31998704387`
- settlement artifact: `9277727825`
- settlement artifact ZIP SHA-256: `3e012815386508a4b0e363fcc10099b3af7550128e737533eae18b7fffeb9d4b`
- target-only label receipt SHA-256: `45d3df41d516da08a1f571a937d4bbf7f6770136094f6962e762bb9e7d458116`
- settlement summary SHA-256: `1977f7b78be0023c4b29637392e2becbeea73fbf9b8b251b47e08f784cc837e1`

该 run 明确执行：

- `B04_labels_opened_in_this_run=359`；
- `non_B04_fixture_ids_requested=0`；
- `label_columns_materialized_for_non_B04_rows_in_this_run=0`；
- 359 场通过 frozen fixture IDs 的 target-only 查询打开 outcome；
- B01/B02/B03 outcome 在该 run 中读取数为 0。

因此 B04 的历史状态转移点明确为：

`PREDICTIONS_FROZEN_LABELS_UNOPENED` → run `31998704387` → `VIEWED`

B04 锁定合同结算结果：

- status: `FAIL_B04_OU25_NO_CONFIRMED_DIRECT_T_INCREMENT`
- n: `359`
- LogLoss delta candidate-baseline: `-0.009181875234092318`
- Brier delta: `-0.003064149231567755`
- RPS delta: `-0.001680583952775756`
- Top1 delta: `+0.002785515320334262`
- Top2 delta: `+0.022284122562674095`
- calendar-date bootstrap90: `[-0.020674795951751247, +0.002643013130652647]`
- primary gate: FAIL，因为 CI upper > 0
- formal_weight: `0`

### 4. 四包 reserve 正式关闭

- closure commit: `54e1c05262f42c731975710c77eb0a0e4dd47a30`
- UTC: `2026-08-17T05:46:08Z`
- local: `2026-08-17 13:46:08 +08:00`
- message: `research: close OU25 Direct-T four-batch reserve`
- status: `CLOSED_NO_CONFIRMATORY_PROMOTION`
- `all_batches_viewed=true`
- `unopened_batches_remaining=0`

四包结果：B01 FAIL / B02 PASS / B03 FAIL / B04 FAIL；四包 LogLoss 点估计均改善，但只有 B02 通过原锁定单包门。

### 5. 后续 post-view closure 再次确认

- commit: `2813b16d2e17f87f4f8e7618e6589dfe85a1a7d8`
- UTC: `2026-08-17T06:10:55Z`
- source run: `32000375197`
- artifact: `9278178012`
- artifact ZIP SHA-256: `4af1f68c4d7769ffc957fbd93d1d9c0102a4e4275ab41842e027686d601d6f73`
- scope rows: `1559`
- batches: B01/B02/B03/B04
- `all_target_labels_already_viewed=true`

## stale 规则

自 B04 settlement run `31998704387` 完成后：

1. 任何“B04 仍未开封 / 仍可作为独立未看标签包”的旧聊天、旧 Airtable 文本、旧 checkpoint、旧手稿，一律标记为 `STALE_AFTER_B04_SETTLEMENT`；
2. 旧文本只可用于还原历史时间线，不得用于当前恢复或决定下一步；
3. 不得再次把 B04 当 holdout、blind reserve、gold-standard reserve 或任何同义确认包；
4. 不得因 B04 的 target-only settlement 是合规的，就把已经 VIEWED 的 B04 恢复为可确认样本；
5. state60 的 gold-standard 永久退役裁决继续覆盖所有旧建议；
6. state61 B01-B03 底座解剖的 `B04 analyzed rows=0` 仍然有效：那是分析 scope 的主动排除，不表示 B04 当时仍未 VIEWED。

## 恢复优先级

关于 B04 是否 VIEWED，只允许使用以下当前裁决：

`B04 = VIEWED since settlement run 31998704387; all four batches closed; no unopened reserve remains.`

任何更老层级出现相反表述，均由本文件覆盖。

## 正式资产边界

- formal model changes: `0`
- formal data changes: `0`
- formal config changes: `0`
- CURRENT changes: `0`
- formal_weight changes: `0`
- new science PASS: `0`
