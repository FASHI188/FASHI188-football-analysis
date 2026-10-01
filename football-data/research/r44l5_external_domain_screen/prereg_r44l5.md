# R44L5 外部赛事域零标签规模/密度筛选（冻结预注册）

## 1. 目的

R44L4 在 WSL 零标签覆盖457/457通过，但开标签后的 draw vs one-goal non-draw 有效样本仅172/65平，低于预注册250场门并在拟合前停止。R44L5 不重用、不放宽 WSL，而是在同一 Hudl/StatsBomb Open Data source pin 上先做全域**零标签**规模与结构密度筛选，避免再次开标签后才发现样本不足。

## 2. 权限边界

- research_only: true
- formal_weight: 0
- CURRENT: V5.2.0，不修改
- Provider/API-Football/付费API/Secret: 0
- target/settlement labels: 0
- model fits: 0
- fixed100/fixed200: 0
- 正式模型/正式数据/config: 0改动

本阶段只允许读取比赛身份字段：match_id、match_date、kick_off、competition、season、home_team、away_team。比赛容器中其他字段不得用于筛选、排序、门控或任何特征。

## 3. 数据身份

- source_repo: hudl/open-data
- source_pin: b0bc9f22dd77c206ddedc1d742893b3bbe64baec
- R44L4 已消费 competition_id=37，固定排除。

## 4. Stage-A 冻结门

候选外部赛事域必须同时满足：

1. 可读取且身份一致；match_id全局唯一；
2. competition seasons >= 4；
3. total match identities >= 720；
4. 至少4个赛季各自 identities >= 150；
5. 所有 team-season 的比赛数中位数 >= 18；
6. team-season 比赛数 P10 >= 10；
7. competition_id != 37。

720不是科学效果门，而是零标签容量门：R44L4 已显示457 identity只能留下172个主目标样本。R44L5 为避免再次明显欠功效，要求外部域身份池显著更大，并且至少4个赛季具有完整联赛级密度，才能支持后续一个fit起点加至少3个时间顺序OOS块。

## 5. 冻结选择规则

- 扫描 source pin 中 competitions.json 声明的全部competition-season。
- 不依据任何比赛结果、目标标签、赛事历史命中率或模型性能选择赛事域。
- 所有通过Stage-A的赛事域全部报告，不事后只保留名字更熟悉的联赛。
- 若0个通过，终态固定：`STOP_R44L5_NO_EXTERNAL_DOMAIN_MEETS_SCALE_DENSITY_GATE`。
- 若>=1个通过，终态：`PASS_R44L5_STAGE_A_CANDIDATES_FOUND`；下一阶段才允许对通过者做lineup/XI/event-schema零标签覆盖，不得直接开标签。

## 6. 防泄漏/防事后救援

Stage-A执行后禁止：

- 因候选不足降低720/150/4赛季/密度门；
- 加回competition_id=37；
- 读取任何目标结果后再改变候选定义；
- 将杯赛/单队切片仅靠总场次堆积冒充完整外部联赛域；
- 直接进入模型拟合。

若Stage-A无候选，应按V5.2.0停止在同一数据源继续寻找等价首发聚合变体，转向信息本质不同的平局信息轴或真正新的外部数据源。
