# R44L7 定位球职责结构：零标签可重建性预注册

## 1. 研究目的

R44L2 已否定“阵型标签+位置计数+XI连续性→机会质量”的同源结构；R44L3 的球员功能包已覆盖负荷、进球/助攻职责、防守中轴、核心缺失、估值与稳定画像，但没有独立测试角球/任意球执行职责；R44L4/R44L5/R44L6又证明当前Hudl公开源缺少多个完整连续赛季，不能继续用同一攻击质量外部域路线硬凑确认。

R44L7切换到信息本质不同的轴：**当前首发中是否仍包含球队近期实际定位球执行核心，以及定位球职责是否集中/稳定**。

本阶段只回答数据可重建性，不回答平局效果。

## 2. 边界

- CURRENT: V5.2.0，不修改。
- research_only=true；formal_weight=0。
- Provider/API-Football/付费API/Secret=0。
- 目标比赛结果字段访问=0。
- 目标比赛event访问=0；目标比赛只读取独立lineup文件。
- 只允许读取目标比赛之前已结束比赛的event文件来建立职责历史。
- model_fits=0；candidate_probabilities=0；threshold_selection_from_labels=0。
- 正式model/data/config/CURRENT改动=0/0/0/0。
- La Liga 2015/16因本对话schema检查时比赛容器已被展示结果字段，整季固定列为VIEWED_EXCLUDED，不进入R44L7未来效果样本。

## 3. 固定数据身份

source=`hudl/open-data@b0bc9f22dd77c206ddedc1d742893b3bbe64baec`

只用三个尚未打开目标结果的完整2015/16赛事域：
- Premier League: competition_id=2, season_id=27；
- Serie A: competition_id=12, season_id=27；
- Ligue 1: competition_id=7, season_id=27。

本阶段允许match容器只访问：match_id、match_date、kick_off、competition、season、home_team、away_team。代码中禁止访问或命名结果字段。

## 4. 冻结目标样本选择（零标签）

每个赛事域按 `(match_date, kick_off, match_id)` 排序：
- 前100场仅作为职责历史热身区，不选为目标；
- 剩余比赛按等距确定性索引取8场；
- 共24个目标match、48个team-target；
- 选样不得读取比分、胜负、净胜球或任何效果字段。

## 5. 职责定义

对每个目标team：
1. 只取该队目标场之前最近5场比赛；
2. 只读取这些历史比赛event；
3. 只统计该队 `Pass` 且 `pass.type.name` 为 `Corner` 或 `Free Kick` 的事件；
4. 按player_id累计执行次数；
5. 目标XI只从目标比赛lineup文件中 `from=00:00` 且 `start_reason=Starting XI` 的球员获得。

形成以下零标签可重建指标：
- prior5定位球执行总次数；
- prior5执行球员数；
- prior5 Top-1执行人份额；
- Top-1执行人是否进入目标XI；
- prior5全部定位球执行中，由目标XI球员承担的历史份额（XI role retention）。

这些不是平局特征效果结论，只是检查职责能否在赛前历史中被稳定重建。

## 6. 冻结可行性门

必须同时满足：
1. 三赛事域identity均>=370；
2. 24/24目标match均能读取lineup；
3. 至少23/24目标match双方均恰11名00:00首发；
4. 至少46/48 team-target拥有完整5场prior历史及对应event文件；
5. 至少41/48 team-target在prior5中可观察到>=3次Corner/Free Kick执行；
6. 48个team-target的XI role retention中位数>=0.60；
7. Top-1定位球执行人进入目标XI的比例>=0.70；
8. label/result keys accessed=0；target event files accessed=0；model fits=0。

全部通过：`PASS_R44L7_SETPIECE_ROLE_ZERO_LABEL_FEASIBILITY`。
任一失败：`STOP_R44L7_SETPIECE_ROLE_DATA_FEASIBILITY`。

## 7. 后续门

零标签PASS后也**不得直接开标签**。下一阶段必须先建立全量、时间顺序、prior-only职责特征覆盖，并预注册基线、候选、chronological/domain OOS和最低有效目标样本门。只有该下一阶段覆盖门再次通过，才可考虑独立标签执行。

## 8. 禁止事后救援

本次执行后不得根据结果：
- 改5场历史窗口；
- 改24场样本数或等距选样；
- 降低3次定位球门；
- 降低0.60或0.70职责保持门；
- 把目标比赛event用于补职责；
- 加入已查看的La Liga 2015/16；
- 读取目标比分后再定义职责。
