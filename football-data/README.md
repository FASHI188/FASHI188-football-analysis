# 足球赛事域数据与审计仓库

本目录保存足球历史数据、赛事域档案、球队描述特征、训练数据集、赛前冻结、计算校验和赛后审计。

## 与正式规则的边界

- 正式规则只保存在 ChatGPT 足球项目内唯一 `CURRENT_唯一正式规则` 文件中。
- GitHub不得存放、复制或替代正式CURRENT。
- 本仓库不自行创造正式概率；它提供可复现输入、硬门控、统一矩阵校验、冻结和审计。

## 固定赛事范围：16个

### 第一批

1. 英超（ENG_PremierLeague）
2. 德甲（GER_Bundesliga）
3. 意甲（ITA_SerieA）
4. 法甲（FRA_Ligue1）
5. 西甲（ESP_LaLiga）
6. 葡超（POR_PrimeiraLiga）
7. 荷甲（NED_Eredivisie）
8. 瑞士超（SUI_SuperLeague）
9. 苏超（SCO_Premiership）
10. 瑞典超（SWE_Allsvenskan）

### 第二批

11. 日职（JPN_J1）
12. 韩K联（KOR_KLeague1）
13. 巴甲（BRA_SerieA）
14. 阿超（ARG_Primera）
15. 美国职业足球大联盟（USA_MLS）
16. 欧冠（UEFA_ChampionsLeague）

其他杯赛按具体比赛临时核验，不自动继承联赛参数。

## 目录

- `raw/`：冻结的上游原始数据或请求哈希清单
- `processed/`：统一字段后的已完成90分钟比赛
- `league_profiles/`：赛事域描述性档案
- `team_strengths/`：时间衰减、主客拆分、近期状态和Elo描述特征
- `training_datasets/`：只使用赛前历史、按时间顺序划分的数据集
- `market_snapshots/`：问题时点的可成交盘口冻结
- `prediction_freezes/`：不可覆盖的赛前正式计算冻结
- `postmatch_audits/`：不可覆盖的赛后评分
- `config/`：赛事注册表、球队特征参数和人工核验别名
- `schemas/`：单场输入、市场、计算输出、冻结和审计契约
- `engine/`：构建、门控、校验、冻结和审计代码
- `tests/`：概率守恒、逐比分结算、防泄漏和复现测试
- `manifests/`：来源哈希、构建状态、平台状态和长期评估
- `docs/`：运行说明与限制

## 核心命令

```bash
export PYTHONPATH=football-data/engine

python football-data/engine/build_team_strengths.py --print-summary
python football-data/engine/build_training_dataset.py --print-summary
python football-data/engine/validate_platform.py --print-summary
```

单场流程：

```bash
python football-data/engine/match_pipeline.py prepare \
  --input match_input.json --output match_context.json

# 在足球项目中按唯一CURRENT真实计算，生成 calculation_output.json

python football-data/engine/match_pipeline.py validate \
  --context match_context.json \
  --calculation calculation_output.json \
  --output validation_report.json

python football-data/engine/match_pipeline.py freeze \
  --context match_context.json \
  --calculation calculation_output.json \
  --validation validation_report.json
```

赛后：

```bash
python football-data/engine/match_pipeline.py audit \
  --freeze prediction_freeze.json --result result.json

python football-data/engine/evaluate_audits.py --print-summary
```

## 硬纪律

1. 默认只处理90分钟含补时。
2. 比赛、附加赛、分组阶段、淘汰赛和两回合状态必须明确。
3. 历史赔率没有原始报价时间戳时，不得充当正式问题时点市场快照。
4. 没有完整同步1X2、亚洲盘和大小球价格时，不得计算EV。
5. 球队描述特征和训练数据不等于模型已晋级，正式权重仍为0。
6. 总进球轨、条件净胜球轨和统一比分矩阵未真实运行并通过审计时，必须输出不可用。
7. 赛后结果不得修改赛前冻结概率。
8. 数据缺失时降级、弃权或No Bet，不得人工补概率。

完整说明见 `docs/PLATFORM_COMPLETION.md`。
