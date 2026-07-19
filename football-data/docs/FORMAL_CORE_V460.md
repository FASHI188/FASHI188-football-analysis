# V4.6.0 非A联合概率核心

## 边界

正式规则只存在于足球项目内唯一 `CURRENT_唯一正式规则`。本仓库实现该规则指定的可执行联合分布主链、时间顺序验证、域级模型工件与审计接口，不复制正式规则正文，也不签发人工A等级。

正式单场盘口固定使用用户提问时实际检索到的当前1X2、亚洲盘和大小球。历史赔率字段只保留研究用途，不进入问题时点冻结，不是本核心生成总进球和比分的必要条件。

## 主链

1. 只读取目标比赛同一赛季、冻结时点以前的比赛；
2. 按时间衰减滚动估计联赛主客基准和球队主客攻防；
3. 直接用负二项分布生成总进球概率；
4. 在每个固定总进球条件下，用Beta-Binomial分配主队进球并自然得到净胜球；
5. 对少数低比分格做强收缩和限幅，只在固定总进球内部重新分配，不能改变直接总进球边际；
6. 形成唯一联合比分矩阵，由该矩阵汇总1X2、0—7+、BTTS、让球和大小球结算；
7. 当前市场只做同时点审计和价格结算，未经独立验证前不改变模型中心。

## 验证

`validation/nested_backtest_v460.py` 执行：

- 每场只见同赛季且早于预测时点的数据；
- 同一天所有比赛先预测，再统一加入结果，防止同轮泄漏；
- 参数只由更早赛季选择；
- 最新不完整赛季从上线参数选择中留出；
- 记录联合比分Log Score、1X2 Brier/RPS、总进球RPS、Top-1/3/5、80%/90%比分集合覆盖和4+/5+/7+尾部；
- 与赛事平均NB基准做配对区块Bootstrap。

验证通过只生成 `NON_A_FORMAL_CORE_AVAILABLE` 域级工件。A等级仍要求CURRENT规定的市场基准、至少8个外层时间折、校准、阵容路线、独立Replay和签名回执。

## 单场输出纪律

- 总进球0—7+可在域级工件有效、当前赛季样本门控通过时输出；
- 比分Top-1始终标注“模型中心比分”；
- 独立EXACT门控未通过时不得写高置信精确比分；
- 当前价格不完整时仍可输出模型分布，但EV固定为No Bet；
- 赛季初样本不足、球队样本不足、域级工件过期或引擎哈希变化时，主链停止并降级。

## 命令

```bash
export PYTHONPATH=football-data/engine:football-data/validation

python football-data/validation/nested_backtest_v460.py --print-summary

python football-data/engine/run_formal_prediction_v460.py \
  --input match_input.json \
  --context-output match_context.json \
  --calculation-output calculation_output.json \
  --validation-output validation_report.json \
  --print-summary
```
