# R44L4 模型执行冻结补充（标签读取前）

- parent prereg SHA-256: `3091d3eb84e1d1f65ef48d319b61cb542fd9ca076bf79cecd154843519ded494`
- zero-label run: `31620958202`
- zero-label HEAD: `120d57d1ee7654ee2cedfff92cfa58f4d13cc635`
- zero-label Artifact: `9151202292`
- zero-label Artifact digest: `sha256:f988259b63756aef81960e3846ae42780f9621fdcb01f1df7af64aca675c4cc4`
- zero-label status: `PASS_R44L4_ZERO_LABEL_COVERAGE`
- labels opened before this freeze: `0`
- model fits before this freeze: `0`

本文件仅补足预注册中未写死的工程语义，不改变候选、赛季、标签、模型、门槛或指标。

## 1. baseline历史不足语义

- rolling5/rolling10 使用严格早于目标日期的最多5/10场已有比赛；不足窗口时使用已有prior比赛，不用未来填补。
- 一支球队在目标比赛前至少需要1场可用prior比赛；主客任一方为0场时，该目标行标为`model_ineligible_cold_start`。
- team xG against由同一prior比赛对手shot xG自然汇总。

## 2. player历史不足语义

- 当前XI固定由lineup position interval中`from == 00:00`识别；必须恰好11人/队。
- 每名当前XI球员只使用最近10次严格prior出场。
- prior minutes为该球员在prior lineup position intervals的持续时间合计；position切换的连续区间相加。
- interval `to`为空时按90:00封口；异常负区间丢弃该player-match分钟，不用事件或未来数据回填。
- player prior minutes=0时，xG/90、xA-proxy/90、shots/90、shot-assists/90全部置0，同时计入`low_history_count`。
- prior minutes<180同样计入`low_history_count`。

## 3. xA proxy冻结语义

- 对prior match事件先建立`shot_event_id -> statsbomb_xg`映射。
- `Pass`事件若含`assisted_shot_id`且能在同一prior match映射到shot，则将该shot xG计给passer作为shot-assisted-xG。
- 不读取shot outcome，不以是否进球改变xA proxy。

## 4. baseline固定字段

B0只允许以下13项：
1. `home_flag`
2. `home_team_roll5_xgf`
3. `home_team_roll5_xga`
4. `home_team_roll10_xgf`
5. `home_team_roll10_xga`
6. `away_team_roll5_xgf`
7. `away_team_roll5_xga`
8. `away_team_roll10_xgf`
9. `away_team_roll10_xga`
10. `roll5_xgf_diff`
11. `roll5_xgf_absdiff`
12. `roll5_xgf_sum`
13. `roll10_xgf_absdiff`

## 5. challenger固定字段

每队固定7项XI聚合：
- `xi_xg90_sum`
- `xi_xa90_sum`
- `xi_xgi90_sum`
- `xi_shots90_sum`
- `xi_shot_assists90_sum`
- `xi_top3_xgi_share`
- `xi_low_history_count`

每项均生成：home、away、home-away、absdiff、sum，共35项；另外只为`xi_xgi90_sum`增加`min(home,away)`。L4总字段数=`13 + 36 = 49`。

不允许在结果后删除某个字段或加入新字段。

## 6. 时间与同日冻结

- 四季所有match按`match_date, kick_off, match_id`排序。
- 同一`match_date`所有比赛先基于当天开始前历史生成特征，再批量更新team/player histories；同日无论kickoff先后都不互相借用。
- target-fold模型参数只由更早完整赛季训练行拟合，不用target赛季前半段标签重拟合。

## 7. 标签打开顺序

模型workflow必须：
1. 重新执行零标签覆盖门并取得PASS；
2. 校验本freeze文件与parent prereg hash；
3. 才允许model脚本访问match容器中的90分钟比分字段；
4. 一旦打开，WSL四季样本视为已消费，不得在本标签上调参后再次称独立外部域确认。
