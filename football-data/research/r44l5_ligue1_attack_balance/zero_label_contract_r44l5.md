# R44L5 Ligue 1 攻击质量/攻击平衡外部域：零标签覆盖合同

- study_id: `r44l5_ligue1_attack_balance_external_domain`
- phase: `ZERO_LABEL_COVERAGE_ONLY`
- source: `hudl/open-data@b0bc9f22dd77c206ddedc1d742893b3bbe64baec`
- competition: Ligue 1, competition_id=`7`
- frozen source seasons: 2015/16 season_id=`27`; 2021/22 season_id=`108`; 2022/23 season_id=`235`
- research_only=`true`; formal_weight=`0`
- Provider/API-Football/付费API=`0`
- 正式model/data/config/CURRENT改动=`0/0/0/0`

本阶段只筛选外部域是否具备足够大的“身份+首发+事件结构”支持，不允许读取比分、胜负、平局标签、净胜球或任何目标字段，不允许拟合模型。

## 允许读取

match容器仅白名单：`match_id`, `match_date`, `kick_off`, `competition`, `season`, `home_team`, `away_team`。
lineup允许：球队/球员身份与position intervals，仅用于判断双方可否唯一识别11名00:00首发。
event允许在确定性schema样本中读取：event type、shot `statsbomb_xg`、pass `assisted_shot_id`；仅验证字段存在性与连接能力，不汇总任何比赛结果标签。

## 零标签门

必须全部通过：
1. 三赛季identity总数 >= 1000；
2. 每赛季identity >= 300；
3. match_id全局唯一且competition/season身份100%匹配；
4. lineup文件覆盖 >= 98%；
5. 双方各11名00:00首发覆盖 >= 95%；
6. 每赛季按`SHA256(R44L5_SCHEMA_20260813|match_id)`固定抽15场；event下载15/15，至少14/15有`Shot.statsbomb_xg`，至少12/15存在`Pass.assisted_shot_id`可连接shot；三季均通过；
7. `label_fields_accessed=0`, `model_fits=0`, `thresholds_selected=0`。

任一失败：`STOP_DATA_COVERAGE_R44L5`。失败时该域仍未消费标签，可另选全新域；不得为了过门降低上述阈值。

若全部通过：只取得`PASS_R44L5_ZERO_LABEL_COVERAGE`，随后才允许另写完整模型预注册与执行冻结；零标签PASS本身不代表预测有效。
