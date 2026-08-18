# 胜平负模型审计交接报告（修正版）

> 用途：供另一个 Codex 静态审计当前 1X2 / 平局问题。  
> 约束：只使用既有代码和既有结果；本次没有训练、调参或重跑 5524 场。正式 CURRENT V5.0.1 未修改。

## 1. 仓库与审计基线

- GitHub：`https://github.com/FASHI188/FASHI188-football-analysis`
- 分支：`main`
- 原始交接报告代码基线：`083d21db7c9b52b5d46e20edb30a5a40e7e0963b`
- 本轮修正计划：`CORRECTION_PLAN.md`
- 历史市场结论统一分类：`RETROSPECTIVE_REFERENCE_ONLY`

## 2. 当前胜平负调用链

```text
历史：football-data/processed/<competition>/*.csv
实时：football-data/evidence/markets_prospective/*.json
        |
        v
完整 1X2 decimal odds
        |
        v
devig: 1/odds 后归一化 -> q={P(H),P(D),P(A)}
        |
        v
pick = argmax(q[home], q[draw], q[away])
        |
        v
V6.47.5 frozen reliability calibration
key=(competition, already-chosen direction, pmax bin)
        |
        v
reliability_score >= 0.55 ? SELECT : ABSTAIN
        |
        v
V6.50.9 research layer (when evaluated):
P_BASE_ERROR >= 0.40 ? ABSTAIN : keep original H/A
        |
        v
final selective execution
```

**关键：selector 从不重新判断 H/D/A。方向在 market argmax 时已经确定。后续 selector 和 V6.50.9 都只是保留/弃权。**

## 3. 决定最终方向的文件、函数和核心代码

### 3.1 去水概率

`football-data/validation/diagnose_1x2_market_anchor_v697.py::_devig()`

```python
raw = {"home": 1.0 / h, "draw": 1.0 / d, "away": 1.0 / a}
total = sum(raw.values())
return {k: raw[k] / total for k in DIRECTIONS}
```

实时等价实现：`football-data/validation/v6_fresh_market_forward_challengers_v6492.py::devig()`。

### 3.2 第一次决定 H/D/A

`football-data/validation/evaluate_hierarchical_market_selector_v6474.py` 约 128-145 行：

```python
probs = {d: float(market[d]) for d in DIRECTIONS}
probs = {d: probs[d] / sum(probs.values()) for d in DIRECTIONS}
pick = max(DIRECTIONS, key=lambda d: probs[d])
```

前向实现 `football-data/validation/v6_fresh_market_forward_challengers_v6492.py::reliability()` 入口同样先执行：

```python
pick = max(D, key=lambda d: q[d])
pmax = float(q[pick])
```

### 3.3 Reliability calibration 与 selector

冻结文件：`football-data/manifests/v6_hierarchical_selector_forward_v6475_freeze.json`

冻结参数：

- selector threshold = `0.55`
- pmax bins = `[1/3,.40,.45,.50,.55,.60,.65,.70,.75,.80,1.0000001]`
- prior strength = `40`
- minimum competition cell n = `20`

`evaluate_hierarchical_market_selector_v6474.py`：

```python
hit = int(r["pick"] == r["actual"])
posterior = (hits + PRIOR_STRENGTH * global_rate) / (n + PRIOR_STRENGTH)
```

前向最终门：

```python
selected = bool(reliability_score >= 0.55)
```

### 3.4 平局被 selected 排除是结构必然，不是偶然漏报

冻结 reliability 证据：

- global `draw|0`: `n=65`, posterior=`0.4318181818`
- global `draw|1..9`: 无样本时 fallback posterior=`0.5`
- 已审计到的 competition draw posterior 最高约 `0.4457`
- selector threshold=`0.55`

因此所有 Draw reliability 均低于门槛。即使 market argmax 先产生 Draw，冻结 selector 也无法 SELECT Draw。

**结论：当前 frozen selector 从代码和冻结参数上结构性排除了 selected Draw。**

### 3.5 V6.50.9 不是 Draw override

`football-data/validation/v6_draw_risk_operating_point_v6509.py`

```python
veto_score = P_BASE_ERROR
veto_threshold = 0.40
```

`football-data/validation/v6_draw_triage_decision_v6508.py::eval_rule()`：

```python
do_draw = bool(draw_active and r['draw_utility'] > 0.0)
...
do_veto = bool(veto is not None and r['p_error_model'] >= veto)
if do_veto:
    decisions['abstain'] += 1
    continue
...
decisions[r['pick']] += 1
```

既有验证中 `draw_active=false`，所以 V6.50.9 的有效作用只有：**高风险H/A弃权器**。

## 4. 两套必须严格分开的 scorecard

### A. FULL_1X2

定义：所有有资格的比赛必须预测 Home / Draw / Away；不允许 ABSTAIN。

未来评估必须保存：

- accuracy
- Log Loss
- Brier Score
- RPS
- confusion matrix
- Home/Draw/Away Precision、Recall、F1
- macro-F1
- balanced accuracy

### B. SELECTIVE_1X2

定义：允许 ABSTAIN。任何 selective accuracy 都不得写进 FULL_1X2。

未来评估必须同时保存：

- accuracy
- executed count
- total eligible count
- coverage
- accuracy-coverage curve
- confusion matrix / class metrics（仅 executed rows）

## 5. 5524 场既有回放指标：不得混报

结果文件：`football-data/manifests/v6_fullseason_2025_replay_v6506_status.json`

### 5.1 FULL_1X2：5110 场历史 market-argmax

- 有效历史 1X2 市场：`5110`
- hits：`2645`
- accuracy：**51.7613%**
- Log Loss：`0.9916575272`
- Brier：`0.5918338409`
- RPS：`0.2016059968`
- predicted Home：`3532`
- predicted Draw：`40`
- predicted Away：`1538`
- actual Home：`2246`
- actual Draw：`1318`
- actual Away：`1546`

这是当前可直接称为**全量三分类 1X2**的市场基准口径。

### 5.2 SELECTIVE_1X2 第一层：1611 场

- executed：`1611 / 5110`
- coverage：**31.5264%**
- hits：`1081`
- accuracy：**67.1012%**
- predicted Home：`1252`
- predicted Draw：`0`
- predicted Away：`359`
- actual Home：`886`
- actual Draw：`311`
- actual Away：`414`
- Log Loss：`0.8312394921`
- Brier：`0.4789456726`
- RPS：`0.1627565839`

正确 `1081`，错误 `530`。由于 Draw predictions=0，311 场 actual Draw 全部属于错误；`311/530=58.68%`。

### 5.3 SELECTIVE_1X2 第二层：V6.50.9 H/A风险弃权

**固定结果名称：精选主客胜准确率70.77%@24.50%市场覆盖率。**

- executed：`1252`
- total market：`5110`
- accuracy：`886/1252 = 70.7668%`
- 市场覆盖率：`1252/5110 = 24.5010%`
- 对全部5524场覆盖率：`1252/5524 = 22.6647%`
- abstain：`359`
- abstained actual Draw：`95`
- abstained opposite H/A：`69`
- abstained originally-correct H/A：`195`
- Draw predictions：`0`
- retained actual Draw：`311-95=216`
- retained errors：`1252-886=366`
- Draw占保留错误：`216/366=59.02%`

所以 V6.50.9 **没有解决平局方向**；它只是牺牲覆盖率提高精选H/A命中率。

禁止使用以下表述：

- “胜平负达到70%以上”
- “平局问题已经解决”
- “完整1X2达到70.77%”

## 6. 现有5524结果缺少完整混淆矩阵

旧版 `v6_fullseason_2025_replay_v6506.py::x12_metrics()` 只保存：

```python
pick_counts
actual_counts
hits
```

因此旧回执不能唯一恢复完整 3×3 confusion matrix，也不能从聚合值唯一计算 Home/Away Precision、Recall、F1。

可确定的只有 selector Draw：

- Precision：N/A（0次预测）
- Recall：`0/311 = 0%`

本轮只修未来评估输出，不重跑5524场；所以旧回执仍必须明确标记这一审计缺口。

## 7. 1548 holdout 的旧 Draw override

代码：`football-data/validation/v6_draw_residual_override_v670.py`

切分：

- fit：2022/23 + 2023/24，`3157`
- validation：2024/25，`1546`
- holdout：2025/26，`1548`
- selected `l2=1000`, threshold=`0.31`

核心逻辑：

```python
if market_pick != "draw":
    p_draw_err = predict(...)
    if p_draw_err >= threshold:
        pick = "draw"
        if truth == "draw":
            original_wrong_draw_captured += 1
        elif market_hit:
            original_correct_lost += 1
```

holdout：

- baseline：`821/1548 = 53.0362%`
- override Draw predictions：`103`
- Draw hits：`33`
- Draw precision：`32.04%`
- 抓回：`33`
- 误伤原正确H/A：`45`
- 净变化：`33-45=-12`
- final：`809/1548 = 52.2610%`

该方案已拒绝。

## 8. 当前阈值、权重、argmax、override状态

| 项目 | 值 | 状态 |
|---|---:|---|
| market direction | `argmax(H,D,A)` | active |
| selector threshold | `0.55` | frozen |
| reliability prior strength | `40` | frozen |
| minimum competition cell n | `20` | frozen |
| class weights in selector | 无 | N/A |
| V6.7.0 Draw override threshold | `0.31` | rejected |
| V6.7.0 L2 | `1000` | rejected |
| V6.50.8 Draw activation | precision>0.5 + positive net utility + >=20 picks | failed/inactive |
| V6.50.9 P_BASE_ERROR veto | `>=0.40` | research H/A risk veto |
| V6.50.9 action | `ABSTAIN` | not Draw |
| active high-precision Draw override | NONE | unsupported |

## 9. 切分、walk-forward与泄漏结论

### V6.47.4 selector

- development 每赛事严格位于 recent benchmark cutoff 之前。
- selector threshold 只从 development 的预声明 grid 选择。
- fixed benchmark 不参与 threshold 选择。

### V6.50.6 replay

- 同一天所有比赛先预测，随后统一 update。
- 未见同日结果泄漏。
- 历史 closing odds 缺原始报价时间戳，因此只能 `RETROSPECTIVE_REFERENCE_ONLY`。

### V6.50.7-V6.51.2 的2025集合重新定性

代码层面没有直接用2025 target labels拟合冻结阈值；但同一2025结果在多轮研究中被反复查看，并据此前结果继续改变后续方案。

因此现在只能称：**时间外风格的重复研究集**。

不能再称：

- untouched final holdout
- 最终盲测
- 真正未接触测试集

后续真正的模型晋级证据仍必须来自独立OOF/rolling-origin外层预测或新的真实前向冻结样本；本报告不在此提出或实现新模型。

## 10. closing odds 的证据边界

历史回放赔率缺原始报价时间，因此：

`RETROSPECTIVE_REFERENCE_ONLY`

它可以用于研究诊断，但不能证明：

- 实时固定赛前时点能够得到同样准确率；
- 当前研究方案可自动晋级正式模型；
- 70.77%可复制为实时全量1X2准确率。

## 11. V6.50.9 原始回执可追溯性

原始回执已恢复到 main：

`football-data/manifests/v6_draw_risk_operating_point_v6509_status.json`

原始 blob SHA：

`ac061398b692329843404f9d86639544b480dcc4`

它是从既有研究分支 `v6507-draw-residual-observable` 的历史blob直接恢复，不是重新执行产生。

修正后的解释层：

- `football-data/manifests/v6_draw_problem_resolution_v6510_status.json`
- `football-data/manifests/v6_draw_problem_resolution_v6512_status.json`

## 12. 三个最可能导致“几乎不预测平局”的原因（按证据强弱）

1. **最强：冻结 selector 在数值上结构性排除 Draw。** Draw reliability posterior/fallback 全低于 `0.55`，所以 Draw 无法进入 selected。
2. **很强：selector 不是三分类器。** H/D/A方向先由 market argmax 决定，selector只SELECT/ABSTAIN；5110场market argmax本身只产生40次Draw，而实际Draw有1318次。
3. **强：后续有效模块继续只做H/A弃权。** V6.50.9预测Draw仍为0；它把359场删除后提高精选H/A准确率，但保留错误中Draw仍占59.02%。

---

## 固定审计表述

- FULL_1X2历史market-argmax：**51.7613%@5110场**。
- 第一层SELECTIVE_1X2：**67.10%@31.53%市场覆盖率**。
- 第二层SELECTIVE_1X2：**精选主客胜准确率70.77%@24.50%市场覆盖率**。
- **平局方向未解决；精选样本当前结构性不输出Draw。**
- **2025为时间外风格的重复研究集。**
- **historical closing odds = RETROSPECTIVE_REFERENCE_ONLY。**
