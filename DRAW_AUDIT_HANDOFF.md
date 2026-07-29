# 胜平负模型审计交接报告

> 目的：供另一个 Codex 直接审计当前“几乎不预测平局”的代码与既有结果。  
> 约束：本文只整理现有代码、冻结文件和已生成回执；未重新训练、调参或重跑 5524 场。

## 1. 仓库与审计基线

- GitHub：`https://github.com/FASHI188/FASHI188-football-analysis`
- 当前分支：`main`
- 本报告创建前观测到的最新 HEAD：`083d21db7c9b52b5d46e20edb30a5a40e7e0963b`
- 说明：本报告本身会产生一个仅文档变更的新 commit；审计代码基线以上述 pre-handoff HEAD 为准。
- 正式 CURRENT：V5.0.1；本文涉及的 V6.x 均为 research / formal_weight=0，除非文件自身另有明确说明。

## 2. 胜平负完整调用链

### 2.1 当前前向冻结链（V6.49.2）

```text
football-data/evidence/markets_prospective/*.json
  one_x_two = {home, draw, away} decimal odds
        |
        v
v6_fresh_market_forward_challengers_v6492.py::devig()
  1/odds -> normalize -> q={P(H),P(D),P(A)}
        |
        v
market argmax: pick=max(home,draw,away)
        |
        v
v6_fresh_market_forward_challengers_v6492.py::reliability()
  frozen V6.47.5 reliability model
  key=(competition, market-pick direction, pmax bin)
  competition Beta-shrunk posterior or global fallback
        |
        v
selector: reliability_score >= 0.55 ? SELECT : ABSTAIN
        |
        v
formal/current draw override: NONE
        |
        v
football-data/forward/v6_fresh_selector_events_v6492.json
  frozen probability + market argmax pick + selected flag
```

**关键事实：**V6.47.5/V6.49.2 不重写 `q`。所谓“校准”是对“market argmax 是否可靠”的命中率校准，不是对 H/D/A 概率本身做重新校准。

### 2.2 历史 2025 整季回放链（V6.50.6）

```text
football-data/processed/<competition_id>/*.csv
  legacy 1X2 closing fields
        |
        v
diagnose_1x2_market_anchor_v697.py::_extract_odds()
  provider priority -> normalized implied probabilities
        |
        v
v6_fullseason_2025_replay_v6506.py::selector_decision()
  exact frozen V6.47.5 threshold/model
        |
        +--> market_all_available metrics (5110)
        |
        +--> selector_selected metrics (1611)
```

### 2.3 当前研究层的 post-selector 处理

- `V6.50.9`：对已 selected 的 H/A 预测估计 `P_BASE_ERROR`；`>=0.40` 时 **ABSTAIN**，不改成 Draw。
- `V6.50.7 / V6.51.0 / V6.51.1 / V6.51.2`：尝试 H/A -> Draw，均被现有验证结果拒绝。
- 因此当前可复核的有效研究政策是：**H/A 保留或弃权；没有激活的高精度 Draw override。**

## 3. 决定最终胜/平/负的文件、函数、行号与核心代码

### A. 市场概率来源：去水

文件：`football-data/validation/diagnose_1x2_market_anchor_v697.py:42-79`

```python
ODDS_TRIPLETS = (
    ("PSCH", "PSCD", "PSCA", "Pinnacle_closing"),
    ("B365CH", "B365CD", "B365CA", "Bet365_closing"),
    ("AvgCH", "AvgCD", "AvgCA", "Average_closing"),
    ...
)

def _devig(h: float, d: float, a: float) -> dict[str, float]:
    raw = {"home": 1.0 / h, "draw": 1.0 / d, "away": 1.0 / a}
    total = sum(raw.values())
    return {k: raw[k] / total for k in DIRECTIONS}

def _extract_odds(raw):
    for hc, dc, ac, label in ODDS_TRIPLETS:
        ...
        if h is not None and d is not None and a is not None:
            return _devig(h, d, a), label
```

前向 live 等价实现：`football-data/validation/v6_fresh_market_forward_challengers_v6492.py:102-110`。

### B. 第一次决定方向：market argmax

文件：`football-data/validation/evaluate_hierarchical_market_selector_v6474.py:128-145`

```python
probs = {d: float(market[d]) for d in DIRECTIONS}
s = sum(probs.values())
probs = {d: probs[d] / s for d in DIRECTIONS}
pick = max(DIRECTIONS, key=lambda d: probs[d])
...
"pick": pick,
"pmax": probs[pick],
```

前向实现：`v6_fresh_market_forward_challengers_v6492.py:227-249` 中同样先执行：

```python
pick = max(D, key=lambda d: q[d])
pmax = float(q[pick])
```

`DIRECTIONS/D` 顺序均为 `("home", "draw", "away")`。Python `max(..., key=...)` 在完全相同 key 时保留第一个最大项，因此精确并列时有顺序性偏置，但现有证据没有显示这是一阶主因。

### C. 可靠度“校准”模型

文件：`football-data/validation/evaluate_hierarchical_market_selector_v6474.py:53-66,165-203`

```python
PMAX_EDGES = (1/3, .40, .45, .50, .55, .60, .65, .70, .75, .80, 1.0000001)
RELIABILITY_GRID = (0.55, 0.575, 0.60, 0.625, 0.65)
PRIOR_STRENGTH = 40.0
MIN_COMP_CELL_N = 20
...
hit = int(r["pick"] == r["actual"])
...
posterior = (hits + PRIOR_STRENGTH * gr) / (n + PRIOR_STRENGTH)
...
if cell and int(cell["n"]) >= MIN_COMP_CELL_N:
    return float(cell["posterior_mean"]), "COMPETITION_SHRUNK_CELL", int(cell["n"])
return float(g["posterior_mean"]), "GLOBAL_FALLBACK_CELL", int(g["n"])
```

冻结模型：`football-data/manifests/v6_hierarchical_selector_forward_v6475_freeze.json`

关键冻结值：

- selector threshold = `0.55`
- pmax bins = `[1/3,.40,.45,.50,.55,.60,.65,.70,.75,.80,1.0000001]`
- prior strength = `40`
- min competition cell n = `20`
- global `draw|0`: `n=65`, posterior=`0.4318181818`
- global `draw|1..9`: `n=0`, posterior=`0.5`

以上 global draw posterior 全部低于 `0.55`。

### D. Selector 最终 SELECT/ABSTAIN

前向：`football-data/validation/v6_fresh_market_forward_challengers_v6492.py:227-249`

```python
threshold = float(freeze.get("selector_threshold") or 0.55)
...
return pick, pmax, score, level, support, bool(score >= threshold)
```

历史回放等价：`football-data/validation/v6_fullseason_2025_replay_v6506.py::selector_decision()`（约 329-351 行）。

```python
pick = max(DIRECTIONS, key=lambda d: q[d])
...
return {
    "pick": pick,
    ...,
    "selected": bool(score >= threshold),
}
```

### E. 冻结最终前向输出

文件：`football-data/validation/v6_fresh_market_forward_challengers_v6492.py:285-304`

```python
append_event(selector_ledger, "SELECTOR_PREDICTION_FROZEN", mid, now.isoformat(), {
    "fixture_identity": fi,
    "prediction": {"probabilities": q, "pick": pick, "pmax": pmax},
    "selector": {
        "threshold": selector_freeze.get("selector_threshold"),
        "reliability_score": score,
        "reliability_level": level,
        "reliability_support": support,
        "selected": selected,
    },
    "governance": {"probability_mutation": False, "formal_weight": 0},
})
```

因此 V6.49.2 的“方向”本身就是 market argmax；selector 只决定是否执行。

### F. 当前研究风险 veto（不是 Draw override）

文件：`football-data/validation/v6_draw_risk_operating_point_v6509.py:36-87`

```python
models = {
    'draw': tri.fit_label(train, lambda r: r['actual']=='draw'),
    'correct': tri.fit_label(train, lambda r: r['actual']==r['pick']),
    'error': tri.fit_label(train, lambda r: r['actual']!=r['pick']),
}
...
draw_active = bool(
    v_draw['draw_pick_count'] >= 20
    and v_draw['draw_override_net_hits'] > 0
    and (v_draw['draw_precision'] or 0) > 0.5
)
...
th = k / 100.0
...
MIN_RETENTION = 0.75
```

最终既有结果选择 `P_BASE_ERROR >= 0.40 -> ABSTAIN`；Draw execution 未激活。

底层决策：`football-data/validation/v6_draw_triage_decision_v6508.py:92-118`

```python
do_draw = bool(draw_active and r['draw_utility'] > 0.0)
if do_draw:
    ... decisions['draw'] += 1
    continue

do_veto = bool(veto is not None and r['p_error_model'] >= veto)
if do_veto:
    ... decisions['abstain'] += 1
    continue

executed += 1
decisions[r['pick']] += 1
hits += int(r['actual'] == r['pick'])
```

## 4. 5524 场回放：现有完整可复核指标

现成回执：

- **分支**：`v6506-2025-replay-observable`
- **路径**：`football-data/manifests/v6_fullseason_2025_replay_v6506_status.json`
- schema：`V6.50.6-fullseason-2025-replay-status-r1`
- 状态：`PASS_RETROSPECTIVE_DIAGNOSTIC`

### 4.1 样本

| 指标 | 现有值 |
|---|---:|
| 2025/2025-26 全量 target | 5524 |
| 赛事域 | 17 |
| 可解析 raw rows | 5524 |
| 有效历史 1X2 市场 | 5110 |
| 缺 1X2 | 414 |
| selector selected | 1611 |
| selector / 有市场覆盖率 | 31.5264% |

### 4.2 两个“全量三分类”口径必须区分

**A. 5524 场 F06 联合矩阵 result Top-1：**

- accuracy = `0.4663287473` = **46.6329%**
- result Brier = `0.6243604992`
- 该回执没有持久化 F06 的 1X2 Log Loss、类别预测数量、混淆矩阵或 per-class Precision/Recall。

**B. 5110 场有历史 1X2 的 market-argmax：**

- hits = `2645 / 5110`
- accuracy = **51.7613%**
- Log Loss = `0.9916575272`
- Brier = `0.5918338409`
- RPS = `0.2016059968`
- predicted Home = `3532`
- predicted Draw = `40`
- predicted Away = `1538`
- actual Home = `2246`
- actual Draw = `1318`
- actual Away = `1546`

### 4.3 Selector 1611 场

- hits = `1081 / 1611`
- accuracy = **67.1012%**
- coverage = **31.5264% of 5110**
- Log Loss = `0.8312394921`
- Brier = `0.4789456726`
- RPS = `0.1627565839`
- predicted Home = `1252`
- predicted Draw = **0**
- predicted Away = `359`
- actual Home = `886`
- actual Draw = `311`
- actual Away = `414`

### 4.4 混淆矩阵：现有结果的审计缺口

`v6_fullseason_2025_replay_v6506_status.json` **没有保存逐格 confusion matrix**。对应实现 `v6_fullseason_2025_replay_v6506.py:194-226` 只累计：

```python
hits += int(pick == actual)
...
by_pick[pick] += 1
by_actual[actual] += 1
...
"pick_counts": dict(sorted(by_pick.items())),
"actual_counts": dict(sorted(by_actual.items())),
```

因此在“不重新跑 5524 场”的约束下，完整 3x3 矩阵不能从现有聚合量唯一恢复。可复核的约束如下：

**5110 market-argmax：**

| Pred \\ Actual | Home | Draw | Away | Pred total |
|---|---:|---:|---:|---:|
| Home | ? | ? | ? | 3532 |
| Draw | ? | ? | ? | 40 |
| Away | ? | ? | ? | 1538 |
| Actual total | 2246 | 1318 | 1546 | 5110 |

并且三条对角线总和 = `2645`。除此之外不可唯一确定。

**1611 selector：**

| Pred \\ Actual | Home | Draw | Away | Pred total |
|---|---:|---:|---:|---:|
| Home | ? | ? | ? | 1252 |
| Draw | 0 | 0 | 0 | 0 |
| Away | ? | ? | ? | 359 |
| Actual total | 886 | 311 | 414 | 1611 |

并且：

- `TP_home + TP_away = 1081`
- 所有 `311` 个 actual Draw 必然落在 Pred Home/Away 两行。

### 4.5 per-class Precision / Recall

由于 5524/5110 回执未持久化 classwise TP/FP/FN，Home/Away Precision 与 Recall **不能从现有聚合结果唯一恢复**。

对 selector 可确定：

- Draw predictions = `0`
- Draw Precision = **N/A（无预测）**
- Draw Recall = **0 / 311 = 0%**

不要用重新读取 5524 场来补这部分；当前审计应把它标记为“结果文件未持久化”。

### 4.6 ECE

V6.50.6 的 5524/5110 回执 **没有保存 ECE**。

现有、但属于另一份 1548 场 holdout 的 draw-probability 校准证据：

- 路径：`football-data/manifests/v6_draw_probability_calibration_v673_status.json`
- holdout n = `1548`
- 1X2 accuracy = `53.0362%`
- Brier = `0.5868019516`
- Log Loss = `0.9840577041`
- draw binary Brier = `0.1904567689`
- draw binary Log Loss = `0.5672916342`
- draw-probability ECE = `0.0118453524`
- interpretation = `MARKET_DRAW_PROBABILITY_ALREADY_HARD_TO_IMPROVE`

该 ECE **不可冒充为 5524 场 ECE**。

## 5. 1611 selector 样本：1081 正确、530 错误、311 平局

数据来源：V6.50.6 回执中的 `f05_market_and_selector.selector_selected`。

- count = `1611`
- hits = `1081`
- errors = `1611 - 1081 = 530`（由现有聚合量直接算术得到，不是重新回放）
- actual Draw = `311`
- selector Draw picks = `0`
- 因此 **311/311 actual Draw 全部必然是 selector 错误**
- 311 / 530 = **58.6792%** 的 selector 错误来自 actual Draw
- 剩余 `219` 个错误是 H/A 方向预测但实际为相反 H/A

### 为什么 selector 一次 Draw 也没有选择

1. market argmax 本身在 5110 场只给 Draw `40` 次（0.7828%），而 actual Draw `1318` 次（25.7926%）。
2. selector 的 reliability key 以 **已经产生的 market argmax direction** 为条件，selector 不重新分类。
3. 冻结 reliability 中 global `draw|0` posterior=`0.4318`；`draw|1..9` 无样本时 fallback posterior=`0.5`，均低于 SELECT threshold=`0.55`。
4. 现有 2025 回执最终确认：40 个 market Draw Top-1 中没有任何一个进入 1611 selected 集。

## 6. 1548 holdout 的平局 override：33 抓回 / 45 误伤 / 净 -12

代码：`football-data/validation/v6_draw_residual_override_v670.py`

结果：`football-data/manifests/v6_draw_residual_override_v670_status.json`

### 6.1 数据切分与选中规则

- fit：2022/23 + 2023/24，n=`3157`
- validation：2024/25，n=`1546`
- holdout：2025/26，n=`1548`
- L2 grid = `(1,10,100,1000)`
- override threshold grid = `0.25..0.60` step `0.01`
- validation 最终选择：`l2=1000`, `threshold=0.31`
- minimum validation overrides = `40`

### 6.2 原 override 代码

`v6_draw_residual_override_v670.py:112-162`

```python
market_pick = r["market_pick"]
truth = r["actual_result"]
market_hit = int(market_pick == truth)
pick = market_pick
if model is not None and threshold is not None and market_pick != "draw":
    p_draw_err = base._predict_binary(model, r["draw_override_x"])
    if p_draw_err >= threshold:
        overrides += 1
        pick = "draw"
        if truth == "draw":
            draw_hits += 1
            if not market_hit:
                original_wrong_draw_captured += 1
        elif market_hit:
            original_correct_lost += 1
...
"paired_net_hits": original_wrong_draw_captured - original_correct_lost,
```

### 6.3 Holdout 结果

Baseline：

- `821 / 1548 = 53.0362%`
- baseline Draw predictions = `3`
- actual Draw = `404`

Override：

- overrides = `100`
- total Draw predictions = `103`
- Draw hits = `33`
- Draw precision = `32.0388%`
- Draw recall = `8.1683%`
- original wrong draws captured = **33**
- original correct H/A picks lost = **45**
- paired net hits = `33 - 45 = -12`
- final hits = `809 / 1548 = 52.2610%`
- accuracy change = `-0.7752 pp`

这 33/45/-12 直接由 `_score()` 的计数定义产生，不是事后解释。

### 6.4 类别权重

该 override 使用 `v6_direct_outcome_mvp_v600.py::_fit_binary()`；实现为普通未加权 binary logistic negative log-likelihood + L2。没有 `class_weight` 或 draw-positive 样本权重。prevalence 只用于初始化 intercept。

核心位置：`football-data/validation/v6_direct_outcome_mvp_v600.py:284-360`（函数 `_fit_binary`）。

## 7. 当前决策阈值、类别权重、argmax 与 Draw override 条件

| 项 | 值/逻辑 | 状态 |
|---|---|---|
| 1X2 概率 | `1/odds` 后三项归一化 | active input |
| 方向 argmax | `max((home,draw,away), key=p)` | active |
| pmax bins | `1/3,.40,.45,.50,.55,.60,.65,.70,.75,.80,1.0000001` | frozen |
| reliability prior strength | `40.0` | frozen |
| min competition cell n | `20` | frozen |
| selector threshold | **0.55** | frozen active research selector |
| selector class weight | 无 | N/A；按 direction 分 cell，不是 class weighting |
| V6.7.0 draw override L2 | **1000** | rejected research |
| V6.7.0 draw override threshold | **0.31** | rejected research |
| V6.7.0 override condition | `market_pick != draw && p_draw_err >= 0.31` | rejected |
| V6.50.8 Draw utility | `P_DRAW - P_BASE_CORRECT > 0` | probe only |
| V6.50.8 Draw activation | picks>=20 AND net_hits>0 AND precision>0.5 | failed / inactive |
| V6.50.8 logistic L2 | `0.01` | research |
| V6.50.8 epochs / lr | `100 / 0.18` | research |
| V6.50.9 risk veto score | `P_BASE_ERROR` | research solution |
| V6.50.9 risk veto threshold | **0.40** | chosen on 2024 validation |
| V6.50.9 min retention | **0.75** | research |
| veto action | **ABSTAIN** | not Draw |
| V6.51.0 Draw re-entry gate | validation combined acc>=0.70, draw precision>=0.50, draw picks>=20 | no passing rule |
| 当前高精度 Draw override | **NONE** | not supported |

## 8. 数据切分与 walk-forward / 泄漏审计

### 8.1 V6.47.4 selector calibration

`evaluate_hierarchical_market_selector_v6474.py:152-162`：每赛事 DEVELOPMENT 只取该赛事 fixed1000 最近两季起始日期之前的历史行。

`evaluate_hierarchical_market_selector_v6474.py:234-244`：selector threshold 只在 DEVELOPMENT 的预声明 grid 中选，选择“最低通过门的 threshold 以最大化覆盖”，不读取 fixed1000 结果来选 threshold。

### 8.2 V6.50.6 5524 回放

`v6_fullseason_2025_replay_v6506.py:26-37` 明确：

- 同一天所有比赛先预测，之后才更新 result-dependent state；
- 2025 target 结果不用于 threshold / weight / bin / smoothing / market transformation / model config；
- V6.47.5 threshold/reliability 已在更早 development 数据冻结。

`replay_f06()` 实现也是按 date 分组，先建立 `frozen` 预测，再 `model.update_batch(frozen)`。

**同日结果泄漏：未见。**

### 8.3 V6.50.8 / V6.50.9 draw-risk 研究

`v6_draw_triage_decision_v6508.py:50-76`：

- train：`date < 2024-01-01`
- validation：2024 calendar year
- target：V6.50.6 exact 2025/2025-26 contract
- 每天先生成 `frozen` records，之后 `state.update_day(day_rows)`

V6.50.9 的 veto threshold 只在 2024 validation 选，2025 target 不参与 threshold selection。

### 8.4 已知时间证据限制

历史 CSV 的 closing 1X2 没有原始 quote timestamp。V6.50.6 已明确把这类赔率定义为：

`RETROSPECTIVE_REFERENCE_ONLY`

因此：

- 现有代码控制了结果/同日更新泄漏；
- 但历史 market quote 不能证明和 live forward 一样的原始冻结时点；
- 这不是“未来赛果泄漏”的证据，但会限制其正式前向可比性。

## 9. 现成结果文件准确路径

### 当前 main 可直接读取

1. `football-data/manifests/v6_hierarchical_selector_forward_v6475_freeze.json`
2. `football-data/manifests/v6_draw_residual_override_v670_status.json`
3. `football-data/manifests/v6_draw_probability_calibration_v673_status.json`
4. `football-data/manifests/v6_draw_problem_resolution_v6512_status.json`
5. `football-data/manifests/v6_draw_rank_attack_nb_v6512_status.json`
6. `football-data/forward/v6_fresh_selector_events_v6492.json`
7. `football-data/manifests/v6_fresh_market_forward_challengers_v6492_status.json`

### 5524 全季回执

该回执未保留在 main，但现成文件仍可复核：

- ref/branch：`v6506-2025-replay-observable`
- path：`football-data/manifests/v6_fullseason_2025_replay_v6506_status.json`
- blob sha：`6eb06ad7c79032e5cd0722c046c1738d83969bff`

不要重新生成；直接从上述 ref/path 读取。

### 对应代码

- `football-data/validation/diagnose_1x2_market_anchor_v697.py`
- `football-data/validation/evaluate_hierarchical_market_selector_v6474.py`
- `football-data/validation/v6_fresh_market_forward_challengers_v6492.py`
- `football-data/validation/v6_fullseason_2025_replay_v6506.py`
- `football-data/validation/v6_draw_residual_override_v670.py`
- `football-data/validation/v6_draw_triage_decision_v6508.py`
- `football-data/validation/v6_draw_risk_operating_point_v6509.py`
- `football-data/validation/v6_direct_outcome_mvp_v600.py`

## 10. 最可能导致“几乎不预测平局”的三个代码/决策原因（按证据强弱）

### 1) **最强证据：最终方向首先被 market argmax 决定，而 market argmax 本身几乎不把 Draw 排第一**

- 5110 场 market sample：Home `3532`、Draw **`40`**、Away `1538`。
- actual Draw = `1318`。
- 代码：`pick = max(DIRECTIONS, key=lambda d: probs[d])`。
- selector 不是三分类器，只是在这个 argmax 结果上做 SELECT/ABSTAIN。

### 2) **强证据：selector 的 Draw reliability 数据结构与 0.55 threshold 组合，进一步把少量 market-Draw Top1 全部过滤掉**

- frozen global `draw|0`: n=`65`, posterior=`0.4318` < `0.55`。
- `draw|1..9`: n=`0`, fallback posterior=`0.5` < `0.55`。
- 2025 回放：market Draw Top1=`40`，selector Draw picks=`0`。
- selector 条件：`selected = reliability_score >= 0.55`。

### 3) **强证据：没有任何激活的 H/A -> Draw reclassification；当前有效后处理只会 ABSTAIN**

- V6.7.0 holdout override：抓回33，但误伤45，净 `-12`，已拒绝。
- V6.50.8/V6.50.9：Draw 执行需 `P_DRAW>P_BASE_CORRECT` 且验证 precision>50%、net hits>0；未激活。
- V6.51.0/1/2：现有验证均未找到安全 Draw re-entry。
- 当前 research solution：`P_BASE_ERROR>=0.40 -> ABSTAIN`，不是改成 Draw。

---

**审计结论边界：**现有证据足以解释“为什么 selected 集几乎/完全不预测平局”；现有 5524 回执没有逐格 confusion matrix 和 classwise TP，因此本文没有在禁止重跑的约束下补造 Home/Away Precision/Recall 或 5524 ECE。
