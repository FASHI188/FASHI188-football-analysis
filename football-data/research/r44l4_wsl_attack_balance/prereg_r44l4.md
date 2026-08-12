# R44L4 外部赛事域：首发攻击质量 × 攻击平衡平局挑战（冻结预注册）

## 1. 身份与边界

- study_id: `r44l4_wsl_attack_balance_external_domain`
- research_only: `true`
- formal_weight: `0`
- target: 90分钟 `draw` vs `one-goal non-draw`；净胜2球及以上不进入主分类评分。
- source domain: StatsBomb/Hudl Open Data，FA Women's Super League，competition_id=`37`。
- source pin: `hudl/open-data@b0bc9f22dd77c206ddedc1d742893b3bbe64baec`。
- frozen seasons: 2018/19 season_id=4；2019/20 season_id=42；2020/21 season_id=90；2023/24 season_id=281。
- 与PR81英超BPS+xGI路线的关系：本轮不是同字段复制。外部域没有FPL BPS；本轮只验证更窄的机制假设——**当前首发XI基于严格prior比赛的攻击产出及双方攻击平衡，是否能给平局vs一球胜负提供可泛化增量**。
- WSL/competition_id=37 在本仓库代码搜索、分支搜索和Airtable维护日志搜索中未发现既有研究记录；若后续发现先前已读标签证据，本研究必须降级为VIEWED development，不得称独立外部域。
- Provider/API-Football/付费API：`0`。
- 正式模型、正式数据、config、CURRENT：`0`改动。

## 2. PIT与确认权限边界

- StatsBomb历史首发/事件文件为赛后开放数据，本轮只做**独立外部赛事域回顾性科学确认**，不是正式赛前PIT证明。
- 当前目标比赛只允许读取身份、日期、主客与当前首发XI身份；目标比赛自身的射门、xG、传球、进球、比分等一律不得进入特征。
- 球员/球队攻击特征只允许由日期严格早于目标比赛的已完成比赛构造；同日比赛先统一冻结全部特征，再统一更新历史。
- 即使本轮科学通过，也保持`formal_weight=0`；没有可证明赛前available_at/first_observed_at的首发输入，不得正式晋级。

## 3. 零标签覆盖门（必须先通过）

覆盖脚本允许下载match容器，但只访问白名单：`match_id/match_date/kick_off/home_team/away_team/season/competition`；禁止访问`home_score/away_score`及任何胜负字段。

必须同时满足：
1. 四个冻结赛季总match identity >= 400；
2. 每个赛季 identity >= 70；
3. match_id全局唯一，赛事/赛季身份100%一致；
4. lineup文件覆盖 >= 98%；
5. 可识别双方各11名00:00首发的比赛 >= 95%；
6. 每赛季按SHA256(match_id)确定性抽10场（共40场）做event schema抽查；抽查中`Shot`且含`statsbomb_xg`、`Pass`且可识别shot-assist/assisted_shot_id的赛季覆盖必须4/4；
7. 覆盖门不得读取目标比分、draw标签或one-goal标签。

任一失败：`STOP_DATA_COVERAGE_R44L4`，不得读标签、不得拟合。

## 4. 特征构造（冻结）

### B0 baseline：球队prior机会结构
只用目标比赛之前的历史事件：
- home flag；
- team rolling5/rolling10 xG for；
- team rolling5/rolling10 xG against；
- opponent rolling5/rolling10 xG for/against；
- home-away prior xG balance与合计强度。

### L4 challenger：B0 + 当前XI严格prior攻击功能
每名当前首发球员仅用其最近10次、日期严格早于目标比赛的出场：
- xG/90；
- shot-assisted-xG/90（以`pass.assisted_shot_id`连接对应shot的`statsbomb_xg`，作为xA proxy）；
- shots/90；
- shot-assists/90；
- prior minutes；低历史标记（<180 prior minutes）。

球队XI固定聚合：
- XI xG/90 sum；XI xA-proxy/90 sum；XI (xG+xA-proxy)/90 sum；
- XI shots/90 sum；XI shot-assists/90 sum；
- 攻击贡献Top3占比；low-history starter count。

双方固定交互仅允许：
- home值、away值、home-away差、绝对差、两队合计；
- `min(home_xgi, away_xgi)`；
- `abs(home_xgi-away_xgi)`。

禁止运行后字段海选、删字段、加阵型计数、加R44L3第三折特征、改窗口。

## 5. 历史分钟与首发定义（冻结）

- current XI：lineups中position interval `from == 00:00` 的11人；若无法唯一得到11人/队，该场不具备模型资格。
- prior minutes：由prior lineup position intervals计算；异常/缺失interval按该场player minutes不可用处理，不使用目标比赛事件反推。
- prior xG/xA/shots/shot-assists：只来自prior match event文件。

## 6. 时间顺序OOS（冻结）

三个目标折：
1. target 2019/20；fit仅2018/19中日期早于该目标赛季的可用行；
2. target 2020/21；fit仅2018/19+2019/20；
3. target 2023/24；fit仅2018/19+2019/20+2020/21。

目标赛季内特征可以随着已结束prior比赛更新，但模型参数在该折开始前冻结，不在target fold内重拟合。

模型资格：双方baseline所需历史可计算，双方当前XI=11；player低历史通过显式count表达，不用未来填补。

## 7. 估计器与参数（冻结）

- `StandardScaler` + `LogisticRegression(C=1.0, penalty='l2', solver='lbfgs', max_iter=4000, random_state=20260813)`。
- 不使用class_weight。
- B0与L4使用完全相同fit/test行和估计器。
- 缺失数值只允许训练折中位数impute；禁止目标折信息参与imputer拟合。

## 8. 样本有效性（冻结）

读标签后但评分前要求：
- 三个target折合计二分类有效样本 >= 250；
- 合计draw >= 60；
- 每折二分类有效样本 >= 50；
- 每折draw >= 10。

不足则结果记为`STOP_UNDERPOWERED_AFTER_LABEL_OPEN`；样本已消费，不得换赛季补救。

## 9. 科学门（冻结）

Primary：binary LogLoss，delta=`L4-B0`，负值改善。

必须全部满足才允许写`EXTERNAL_DOMAIN_SIGNAL_PASS`：
1. pooled delta LogLoss < 0；
2. 5000次match-cluster bootstrap（seed=20260813）90% CI upper < 0；
3. 至少2/3 target folds delta LogLoss < 0；
4. pooled Brier delta <= +0.002；
5. ROC-AUC不得材料性下降：pooled delta AUC >= -0.01。

辅助报告：PR-AUC、ROC-AUC、Brier、校准斜率/截距、每折指标；另单独报告draw vs one-goal准确率与按L4 score Top10%/Top15%的平局富集，但这些不得覆盖Primary gate。

## 10. 防事后救援

执行后禁止：
- 改C、solver、fold、season、baseline、窗口、标签定义；
- 根据结果只保留某些球员字段；
- 选最好赛季/球队；
- 改成class_weight或阈值强制报平；
- 将两球以上胜负加入/移出以救分；
- 在同一已读WSL标签上重新称独立确认。

若FAIL，本外部域同法候选关闭；若PASS，也只能说明外部域回顾性增量成立，`formal_weight=0`，还需严格forward/PIT确认才可能进入正式链。
