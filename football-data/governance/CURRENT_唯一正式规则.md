# 足球3唯一正式规则（CURRENT）

> 状态：**ACTIVE / CURRENT / UNIQUE FORMAL RULE**  
> 激活日期：2026-08-22  
> 事实锚点：正式核心 / R5 HEAD `e5ea79463ca1c1fb3fc24e818b0be171db8aed8b`；GitHub 冷启动治理基线 HEAD `9edc4819749f0178dfb57d5f56388470394b34ee`；运行时必须重新核对当前 branch / HEAD。  
> 来源：依据现存正式资产保守重建，并由用户审阅后明确授权激活；不是找回的旧原文。  
> 效力：本文是足球3项目范围内唯一正式 CURRENT；不因激活而授权修改模型、训练、真实评分、发布、合并或更新 Airtable。

## 1. 权威顺序与适用边界

足球3的事实和执行权威按以下顺序解释：

1. 用户当前明确指令；
2. GitHub 指定仓库、分支和 exact-HEAD 的实时事实；
3. GitHub 工作树内唯一、可读、经明确激活的正式 CURRENT；
4. GitHub Actions、Artifact、PR 与不可变审计证据；
5. Airtable 记录、历史交接、旧报告和说明性文档只作审计参考，不得覆盖 GitHub 当前事实。

GitHub 是足球3唯一正式持久化来源。正式 CURRENT 的唯一允许路径为 `football-data/governance/CURRENT_唯一正式规则.md`。正式模型、评分或预测任务开始时若该路径不存在、不可读，或仓库中 CURRENT 候选数量不等于 1，必须停止；不得用本机副本、README、历史报告、代码默认值或未激活草案替代。

## 2. 当前正式状态

- 当前正式核心为 `football_v460_engine.py` 所实现的 V4.6 系列核心；配置声明版本为 `V4.6.2-core-2`。
- 当前机器可读注册表、正式清单和模型目录一致覆盖 17 个 competition domain；沙特联不在现有正式 artifact 范围内。
- 17 个现有域的状态均为 `NON_A_FORMAL_CORE_AVAILABLE`；A-grade 签名收据为 0。
- C072-N20 当前状态为 `PILOT_NO_SIGNAL / PARK`，`formal_weight=0`。
- R5 / `football3_hda` 的通过只代表工程与研究层验收，不代表科学晋级、准确率提升、平局问题解决或正式权重变化。
- GitHub 分支内的全覆盖路由 `formal-cold-start-v1` 已作为候选默认测算入口；缺少正式证据时只产生 `formal_weight=0` 的覆盖性基准分布。任何网页 GPT 或服务只有真实调用该 GitHub 版本对应的执行接口时，才能声称使用了足球3引擎。
- 覆盖缺口存在完整、同步且符合 PIT 与来源独立性要求的 1X2、亚洲盘和大小球快照时，可进入 `MARKET_ANCHORED_COLD_START`；该状态仍为 `formal_weight=0`、`exact_gate=false`、`No Bet`，不属于正式核心预测。
- 静态验证、单元测试、GitHub Actions 或可用 artifact 均不能单独授予科学晋级。

## 3. 结算口径与预测冻结

- 比赛结算口径为常规 90 分钟并包含伤停补时，不包含加时赛和点球大战。
- 每次预测必须显式指定目标 competition、目标赛季、主客队和 kickoff / cutoff；不得自动回退到上一赛季球队强度。
- 进入球队强度计算的数据必须属于目标赛季，并严格早于预测 cutoff。
- 对只有日期、没有可靠开球时间的数据源，同一自然日全部排除；不得先看当天赛果再预测当天其他比赛。
- 预测前必须完成问题时点的数据新鲜度和已完赛场次数核对；缺少官方可追溯的 freshness 证据时降级或停止。
- 临时补入尚未落库的已完赛结果时，必须同赛季、来源可追溯、观测时间早于冻结时点，并在运行结束后移除临时覆盖。

## 4. 正式核心计算链

### 4.1 数据与样本硬门

- 球队强度只使用目标 competition、目标赛季、cutoff 之前的数据。
- competition 当前赛季历史少于 30 场时拒绝正式预测。
- 主队当前赛季主场或客队当前赛季客场原始样本少于 2 场时拒绝正式预测。
- `minimum_team_effective_matches_for_stable = 6.0` 只用于稳定度和置信度判断，不得用来绕过原始样本硬门。
- 不得把上一赛季球队强度、未来比赛、其他 competition 的球队身份或未知球队静默注入当前球队强度。

### 4.2 Artifact 与完整性硬门

- 必须存在目标 competition 对应的正式 artifact、验证报告和目标赛季 PIT 参数。
- artifact 的 operational status 必须符合正式核心允许状态；引擎、配置、模型和报告的版本与 hash 必须一致。
- 缺 artifact、缺报告、hash 不一致、状态不合格、目标赛季 PIT 参数缺失或 schema 不合法时一律 fail closed。
- 禁止用默认参数、临时常量或人工猜测静默替代缺失的正式 artifact。
- 上述硬门决定结果能否标记为正式核心预测。普通覆盖缺口可以降级到明确版本化的全覆盖基准；hash、schema、验证报告等完整性故障不得降级，仍须 HARD_FAIL。

### 4.3 单一比分矩阵

正式中心依次执行：

1. 时间衰减后的联赛主客场基线；
2. 球队场地主客攻防强度，并进行受约束收缩；
3. 直接 Negative Binomial 总进球分布；
4. 在总进球条件下用 Beta-Binomial 分配主队进球；
5. 只在总进球内部进行强收缩、有上下界的低比分修正；
6. 由同一比分矩阵统一导出 1X2、大小球、BTTS、亚洲盘结算和比分集合。

关键现行参数包括：最大精确总进球 25、半衰期默认 120 天、球队先验强度 6、离散度先验 30、NB `k=10`、Beta-Binomial concentration 14、低比分 shrinkage 0.25、低比分乘数边界 `[0.8, 1.2]`、期望进球边界 `[0.15, 4.5]`。

任何派生市场都不得另起一套互相矛盾的概率中心。

## 5. 输出标签和可行动性

- 正式核心可输出比分矩阵、1X2、大小球、BTTS、top scores 和比分集合。
- 输出的单一比分只可标记为 **model center**，不是“确定比分”。
- 在独立 exact-score 验证完成前，`exact_gate` 必须保持 false。
- B/C/D 等置信度只描述当前样本和阵容信息条件，不授予 A-grade。
- 没有完整、同步、可追溯的当前市场价格与相应收据时，EV / 投注结论必须为 **No Bet**。
- 所有 competition、赛季和球队的明确比赛请求默认必须返回一套内部一致的比分分布。缺关键证据时必须降级并明确标记证据等级，不得把覆盖性基准冒充正式核心概率。

## 6. 市场信息与可选桥接层

- 当前赔率和盘口默认用于审计、结算及候选比较，不得无收据地改变正式概率中心。
- 对正式核心无法覆盖的比赛，完整、同步、问题时点可用且来源独立的 1X2、亚洲盘和大小球快照，可以通过现有非冗余约束与最小 KL 投影重塑覆盖性全球基准；不得改变已通过正式核心硬门的概率中心。
- 盘口锚定冷启动只解决未知联赛或未知球队全部输出相同概率的问题。同一组盘口既作为概率锚点，就不得再被当作独立证据计算 EV；该路径一律 `No Bet`。
- 盘口缺项但不存在时间、来源或完整性冲突时，保留 `UNINFORMED_GLOBAL_BASELINE`；未来数据、异步快照、虚假来源独立性或其他完整性错误必须 `HARD_FAIL`。
- 只有 competition + season 绑定的 LOMO / OOS 收据通过，且当前 1X2、AH、OU 快照完整、同步、可追溯时，市场协调候选才可进入受控的概率或 EV 路径。
- 下一赛季参数桥接只允许携带经 hash 绑定、收据验证的超参数；不得滚动上一赛季球队强度，不得绕过当前赛季 competition 和球队样本硬门。
- 历史治理兼容层最多改变旧激活标签的兼容解释，不能发现、复制、认证或替代当前正式 CURRENT，也不能改变概率、价格或权重。

## 7. 新赛季与新联赛冷启动

所有明确比赛请求按以下顺序自动路由：

1. `STABLE_CURRENT_SEASON`：当前赛季 competition 与球队场地样本达到原硬门，继续走未修改的正式核心。
2. `PRIOR_SEASON_SHRINKAGE`：存在显式、版本化、hash与PASS收据绑定且适用域匹配的上赛季先验时，按当前有效样本单调递减地收缩。
3. `GENERIC_VALIDATED_FALLBACK`：不存在上赛季先验但存在 competition、赛季、球队身份均在验证范围内的通用artifact时，使用验证后的通用回退。
4. `MARKET_ANCHORED_COLD_START`：正式覆盖证据不足但存在完整、同步、PIT 合格且来源独立的 1X2、亚洲盘和大小球快照时，用最小 KL 投影重塑覆盖性比分矩阵；必须标记 `LOW_MARKET_ANCHORED_UNVALIDATED`、`coverage_only=true`、`formal_weight=0`、`exact_gate=false`、`same_market_ev_allowed=false`、`No Bet`。
5. `UNINFORMED_GLOBAL_BASELINE`：以上证据均不存在或盘口仅为非冲突性缺项时，仍返回版本化全球基准比分分布；必须标记 `VERY_LOW`、`coverage_only=true`、`team_strength_evidence=false`、`formal_weight=0`、`exact_gate=false`、`No Bet`。
6. `HARD_FAIL`：输入不完整、错误cutoff、未来/同日泄漏、artifact或receipt被篡改、hash/schema/验证报告/PIT绑定错误、盘口时间或来源完整性冲突等问题仍拒绝输出。

全覆盖解决的是“每场均可测算”，不自动解决准确率。全球基准不得被称为正式核心预测、球队强弱预测、科学晋级或可投注结论。

## 8. 科学验证与晋级

- 验证必须严格按时间顺序：同日先预测、后加入赛果；调参仅使用更早赛季；最新不完整赛季保留作外层检验。
- 必须和冻结基线及适当市场基线比较，检查校准、稳定性和不同 competition / season 的外推表现。
- A-grade 至少要求 8 个 outer folds、至少 200 个 outer predictions、校准证据、阵容路径、独立复放和签名收据；最低运行样本 100 不能替代 A-grade 门槛。
- 训练基础设施存在不等于已经训练；工程全绿不等于模型有效；局部历史改善不等于严格 prospective OOS 改善。
- 没有新的明确授权，不读取本任务禁止的真实标签，不训练、不调参、不真实评分、不访问 provider / secret，不生成生产 artifact。

## 9. 明确禁止

- 禁止静默默认参数、未来数据、赛后回填泄漏和错误 cutoff；全覆盖全球基准必须有显式版本、配置hash和降级标签。
- 禁止把上赛季球队强度当作当前赛季球队强度直接续用。
- 禁止在无有效收据时用市场价格改变正式中心或生成可投注结论。
- 禁止把 challenger、pilot、engineering pass、research pass 写成 `SCIENTIFIC_PASS`、`MODEL_IMPROVED` 或 `DRAW_SOLVED`。
- 禁止因本正式规则存在而解锁、修改、Ready 或 merge PR #334 / R5 分支。
- 禁止把本正式规则只留在本机、聊天、Airtable 或未绑定文件中；GitHub 指定路径是唯一正式副本，本机副本一律非权威。

## 10. 将来变更或重新激活前的强制检查清单

本节规定未来流程，不构成修改当前规则的授权。任何变更或重新激活必须另获用户明确批准，并至少完成：

1. 用户逐条审阅并批准规则变更内容；
2. 重新核对 GitHub 仓库、目标分支、exact HEAD 和 PR 状态；
3. 确认 GitHub 工作树内不存在另一份正式 CURRENT；
4. 将经批准文本写入唯一约定路径 `football-data/governance/CURRENT_唯一正式规则.md`；
5. 验证 GitHub 工作树内正式 CURRENT 候选计数恰好为 1 且可读；
6. 在机器可读治理合同中记录 CURRENT Git blob SHA、激活时间、适用基线 HEAD 和批准边界；
7. Airtable 仅在另行授权时作为审计镜像更新，且不得覆盖 GitHub 事实；
8. 激活操作本身不得顺带修改模型、训练、发布、合并或解锁 PR。

## 11. 本正式规则的证据来源

| 证据 | 本正式规则采用的事实 |
| --- | --- |
| `AGENTS.md` | GitHub 唯一持久化权威、唯一正式规则要求、工程与科学分离 |
| `README.md` | GitHub 保存唯一正式 CURRENT，禁止本机或外部副本获得正式权威 |
| `football-data/config/formal_core_v460.json` | V4.6.2-core-2 样本门、参数和验证门 |
| `football-data/manifests/formal_core_v460_status.json` | 17 域、全部 NON_A、A 收据为 0 |
| `football-data/config/platform_registry.json` | 17 域、结算口径、正式规则位置和已知缺口 |
| `football-data/engine/football_v460_engine.py` | 正式数据、artifact、PIT、比分矩阵和 fail-closed 行为 |
| `football-data/engine/run_formal_prediction_v460.py` | 显式赛季、freshness、临时结果覆盖和运行后验证 |
| `football-data/engine/run_formal_prediction_actionable.py` | 可行动包装层及 receipt-gated 候选路径 |
| `football-data/engine/formal_next_season_parameter_runtime_v470.py` | 仅超参数滚动、禁止球队强度滚动 |
| `football-data/engine/formal_governance_runtime_v470.py` | 历史兼容层不具当前规则权威 |
| `football-data/engine/formal_ev_lomo_gate_v470.py` | 市场协调和 EV 的 LOMO / OOS 收据门 |
| `football-data/engine/formal_cold_start_candidate_v1.py` | 冷启动四级证据路由及全覆盖基准状态 |
| `football-data/engine/market_anchored_cold_start_v1.py` | 覆盖缺口的同步盘口校验、非冗余约束、最小 KL 投影与同价 EV 禁止 |
| `football-data/engine/run_universal_prediction_candidate_v1.py` | 默认先尝试正式核心、普通覆盖缺口按可用证据降级、完整性故障保持硬失败 |
| `football-data/config/formal_cold_start_candidate_v1.json` | 显式版本化全球基准、盘口锚定合同、降级标签和 formal_weight=0 输出合同 |
| Airtable 历史审计记录 `recI64HZSQddCd7ms` | 仅作既有状态版本 16 的历史参考；不得覆盖 GitHub 当前事实 |

## 12. 已知文档漂移

`PLATFORM_COMPLETION.md` 有一处说明性文字仍写 16 个域；当前 `platform_registry.json`、`formal_core_v460_status.json` 和实际正式模型目录三者一致为 17 个域。因此本正式规则采用机器可读的当前 17 域事实，并把“16”视为未同步的旧文案，不据此新增或删除任何域。

---

**激活依据：** 用户已于 2026-08-22 明确授权唯一正式 CURRENT，并进一步明确要求所有正式修改和治理全部落实到 GitHub、不得以本机文件作为正式成果。  
**当前状态再次确认：** GitHub CURRENT 已激活；所有明确比赛请求可由 GitHub 候选统一入口进入测算，完整合格盘口可生成比赛特异的覆盖性分布，盘口不可用时降级为极低置信度全球基准。网页 GPT 未真实调用执行接口时不得冒充模型输出。

