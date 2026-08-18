# 足球赛事域数据与审计仓库

本目录保存足球历史数据、赛事域档案、球队描述特征、训练数据集、研究资产、前瞻冻结、计算校验和赛后审计。

## 与项目权威链的边界

- 实时施工状态只在 Airtable《当前状态》唯一激活记录中；GitHub 不保存第二份动态 current state。
- 用户当前明确指令决定是否允许产生副作用。
- 正式科学规则只保存在项目作用域内唯一 `CURRENT_唯一正式规则`；GitHub 不得复制或替代它。
- 本仓库提供可复现输入、研究/运行代码、硬门控、冻结和审计；工程可用不自动等于科学晋级。
- 历史 manifest/config 即使文件名含 `current` / `active` / `runtime_truth`，也不能因此获得当前任务或授权权威。

## 当前注册赛事域：17个

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
11. 挪超（NOR_Eliteserien）
12. 日职（JPN_J1）
13. 韩K联（KOR_KLeague1）
14. 巴甲（BRA_SerieA）
15. 阿超（ARG_Primera）
16. 美国职业足球大联盟（USA_MLS）
17. 欧冠（UEFA_ChampionsLeague）

注册表和实际平台状态是赛事域数量的运行事实来源；README 只作导航说明。其他杯赛按具体比赛临时核验，不自动继承联赛参数。

## 目录职责

### 数据与派生资产

- `raw/`：冻结的上游原始数据或请求哈希清单。
- `processed/`：统一字段后的已完成90分钟比赛。
- `training_datasets/`：赛前历史、按时间顺序构建的训练数据集。
- `league_profiles/`、`team_strengths/`、`lineups/`、`player_xi_links/`：赛事域和球队/阵容描述资产。
- `evidence/`：市场、阵容、外部源和 prospective/retrospective 证据。
- `forward/`：前瞻事件、预测/冻结类运行输出；其中内容仍受各自科学门控约束。
- `cache/`、`.cache/`、`tmp/`、`scratch/` 与各类 `_local/`：本地/临时资产，不得提交或被当作正式状态源。

### 代码与研究

- `engine/`：可复用核心构建、门控、概率/结算、冻结和审计实现。历史版本号模块可因 hash binding/复现而保留，但版本号不代表“当前”。
- `runtime/`：显式运行/activation 入口；可能产生运行 artifact 的动作必须显式调用并遵守项目授权，详见 `runtime/README.md`。
- `ingestion/`：上游数据获取/转换入口。
- `validation/`：默认只读的验证、诊断和历史版本审计。需要生成 receipt 的 validator 必须显式使用 `--write-receipt` 一类开关。
- `research/`：研究脚本、研究契约和研究说明；没有自动晋级权。
- `models/`：formal core / challenger 等模型资产；目录名或版本号不自动赋予 formal_weight。
- `tools/`：一次性修复和维护工具。
- `tests/`：概率守恒、逐比分结算、防泄漏、数据接入和复现测试。

### 契约与治理证据

- `config/`：赛事注册表、身份/来源 registry 和工程研究 profile。config 不得充当动态项目状态。
- `schemas/`：输入、市场、计算输出、冻结和审计契约。
- `manifests/`：构建/运行 receipt 和历史评估证据；manifest 证明事实，不决定当前项目任务。
- `governance/`：football-data 内部治理/审计资产；不替代根目录 `AGENTS.md` 或 Airtable《当前状态》。
- `docs/`：运行说明与限制；资产生命周期见 `docs/ASSET_LIFECYCLE.md`。
- `control/`：历史/显式触发类控制资产；不得作为项目动态施工状态源。

## 核心命令

```bash
export PYTHONPATH=football-data/engine

python football-data/engine/build_team_strengths.py --print-summary
python football-data/engine/build_training_dataset.py --print-summary
# 默认只读；只有显式 --write-receipt 才更新 platform_status.json
python football-data/engine/validate_platform.py --check-only --print-summary
```

单场流程：

```bash
python football-data/engine/match_pipeline.py prepare \
  --input match_input.json --output match_context.json

# 在足球项目中按唯一 CURRENT 真实计算，生成 calculation_output.json

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
5. 球队描述特征和训练数据不等于模型已晋级；正式权重只按唯一正式 CURRENT 和正式晋级证据解释。
6. 总进球轨、条件净胜球轨和统一比分矩阵未真实运行并通过相应正式审计时，不得把工程存在表述为科学可用。
7. 赛后结果不得修改赛前冻结概率。
8. 数据缺失时降级、弃权或 No Bet，不得人工补概率。
9. Validation 的静态绿灯不能替代行为测试、严格 OOS 或科学 PASS。
10. 大型 payload 优先外部/Artifact 保存，Git 只保留必要的不可变证据、哈希、来源和可复现契约；既有冻结数据不因本规则自动删除。

完整平台说明见 `docs/PLATFORM_COMPLETION.md`；资产策略见 `docs/ASSET_LIFECYCLE.md`；具体科学硬规则以唯一正式 CURRENT 为准。
