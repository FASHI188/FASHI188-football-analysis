# 足球数据与审计平台补齐说明

## 1. 边界

本仓库不保存正式规则，不复制 `CURRENT_唯一正式规则`。正式概率必须由足球项目内当前唯一正式规则真实运行后产生。本仓库负责：

1. 冻结并清洗历史赛事数据；
2. 生成赛事域描述性档案；
3. 生成球队时间衰减、主客拆分和Elo描述特征；
4. 构建严格按时间顺序、只使用赛前历史的训练数据集；
5. 校验单场身份、冻结时点、盘口、阵容和任务证据；
6. 校验正式计算输出及统一比分矩阵；
7. 保存不可覆盖的赛前预测冻结；
8. 赛后计算Log Score、Brier、RPS和Top-k，不倒灌结果。

## 2. 已补齐的系统层

### 2.1 固定赛事注册表

`config/platform_registry.json` 固定当前16个赛事域及其限制。新增赛事必须显式更新注册表，不能临时混入。

### 2.2 球队动态描述层

`engine/build_team_strengths.py` 读取每个赛事域的已清洗比赛，生成：

- 180天半衰期的长期衰减指标；
- 主场、客场和整体拆分；
- 最近5场、10场；
- 固定参数Elo描述值；
- 样本不足、主客拆分不足和长期未参赛状态；
- 输入文件SHA-256和配置哈希。

该层状态固定为“描述特征，正式权重0”，不得直接输出概率。

### 2.3 防泄漏训练数据层

`engine/build_training_dataset.py` 在每场比赛发生前计算特征，再写入该场标签。禁止随机拆分，固定按赛季时间顺序划分训练、验证、测试或前瞻留出集。

该数据集只解决“可训练、可复现”的基础设施问题，并不代表模型已经训练或晋级。

### 2.4 单场冻结输入层

`engine/match_pipeline.py prepare` 检查：

- 赛事、主客、开球时间、90分钟结算口径；
- 两回合状态；
- 冻结时点早于开球；
- 1X2、亚洲让球、大小球完整价格；
- 市场来源、集团相关性、时间同步和可成交性；
- 官方或预计阵容证据；
- 对应球队动态描述特征。

盘口缺失或不同步时，EV门控关闭；预计阵容只能部分通过。

### 2.5 正式计算输出校验

`engine/match_pipeline.py validate` 不计算概率，只审查正式计算结果：

- 1X2和0—7+概率守恒；
- 比分矩阵单元唯一、非负、总和为1；
- 赛果、总进球和BTTS边际与同一矩阵一致；
- 亚洲盘和大小球逐比分结算一致；
- 文字Top-1与矩阵Top-1一致；
- 市场协调存在真实先验、约束、目标函数、收敛和残差记录；
- 总进球或比分主链未通过时，强制输出固定不可用文本；
- EV必须使用冻结时点的完整可成交价格。

### 2.6 不可覆盖冻结与赛后审计

`freeze` 生成内容哈希并拒绝覆盖同一冻结。`audit` 只读取赛前冻结，计算：

- 1X2：Log Score、Brier、RPS和Top-1；
- 总进球：Log Score、RPS、Top-1和Top-2；
- 比分：Log Score、Top-1、Top-3和Top-5。

`engine/evaluate_audits.py` 汇总长期表现，但准确率不能替代严格适当评分，也不能因单场命中晋级模型。

## 3. 仍然不能人工补齐的外部证据

以下不是代码缺口，而是当前没有真实数据或没有完成前瞻验证：

- 原始时间戳完整且同步的历史1X2、亚洲盘、大小球档案；
- 历史首发、伤停、停赛、轮换和任务状态快照；
- 已完成时间顺序样本外验证并晋级的直接总进球模型；
- 已完成时间顺序样本外验证并晋级的条件净胜球模型；
- 瑞士超、苏超、阿超和MLS的完整阶段标签；
- 日职2026特殊过渡赛事数据；
- 欧冠跨联赛实力转换的独立、已验证参数。

这些缺口出现时，系统必须降级、弃权或No Bet，不能用人工数字填充。

## 4. 标准单场流程

```bash
python football-data/engine/match_pipeline.py prepare \
  --input match_input.json --output match_context.json

# 在足球项目内读取唯一CURRENT并真实计算，生成 calculation_output.json

python football-data/engine/match_pipeline.py validate \
  --context match_context.json \
  --calculation calculation_output.json \
  --output validation_report.json

python football-data/engine/match_pipeline.py freeze \
  --context match_context.json \
  --calculation calculation_output.json \
  --validation validation_report.json

python football-data/engine/match_pipeline.py audit \
  --freeze prediction_freeze.json \
  --result result.json
```

## 5. 模型晋级仍需完成的验收

训练数据已经具备，但正式晋级仍需：滚动时间窗、跨赛季、多个赛事域、Log Score、Brier、RPS、校准斜率与截距、分组校准、尾部质量、比分Top-k和漂移稳定性。未完成前，所有新模型仍是挑战层，权重0。
