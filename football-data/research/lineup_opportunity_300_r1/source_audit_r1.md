# 首发结构 × 预期机会量 — 数据源审计 R1

## 数据身份

- source: `vaastav/Fantasy-Premier-League`
- pinned source commit: `8c97b2adb123863c3dd581e730f1360e89815ac2`
- season: 2025-26 EPL
- audit status: `PARTIAL_SOURCE_AUDIT`
- formal_weight: 0
- PIT historical lineup timestamp: unavailable / unverified

## 赛季 fixture 冻结

`fixtures.csv`：

- rows: 380
- unique fixture ids: 380
- finished=True: 380
- latest-300 selection: sort by `(kickoff_time, id)`, keep last 300
- first selected kickoff: 2025-10-24 19:00 UTC
- last selected kickoff: 2026-05-24 15:00 UTC
- selected events: GW9-GW38

注意：该季存在双赛周/空赛周，event内比赛数并非始终10；因此正式样本冻结基于 kickoff + fixture id，而不是基于“30个GW×10场”的假设。

## GW player schema

已核验字段：

- `fixture`
- `kickoff_time`
- `team`
- `position`
- `element`
- `was_home`
- `starts`
- `minutes`
- `expected_goals`
- `expected_assists`
- `expected_goal_involvements`

`starts` 在数据字典中定义为 1=started, 0=substitute，因此不再使用旧研究中的 `minutes>=60` 代理首发。

## 零标签 pilot

已本地读取：GW9、GW10、GW11、GW12、GW38，共50场、100个team-match。

原始数据发现：

- 发现1条重复的 `(fixture, element)`：fixture=83、element=100、Junior Kroupi；
- 未去重时会把 Bournemouth fixture=83 错算为12名首发；
- 预注册清洗规则因此固定为：先按 `(fixture, element)` 完全去重，再进行首发人数检查。

去重后：

- 100/100 team-match = 11 名 `starts=1`；
- 100/100 team-match = 恰好1名 GK；
- pilot 首发结构恢复率 = 100%。

## FPL positional shell pilot

以下只是FPL位置分类计数，不是战术formation：

- 4-5-1: 50/100
- 5-4-1: 18/100
- 3-6-1: 10/100
- 4-4-2: 9/100
- 5-3-2: 4/100
- 4-6-0: 3/100
- 5-5-0: 2/100
- 6-3-1: 1/100
- 3-5-2: 1/100
- 3-7-0: 1/100
- 4-3-3: 1/100

因为FPL把大量实际边锋/攻击手归为MID，`4-5-1`等只能解释为 FPL role shell，禁止直接写成球队真实阵型。

## 当前数据门结论

- fixture sample freeze: `PASS`
- source schema: `PASS`
- pilot duplicate audit: `WARNING_RESOLVED_BY_PREREG_DEDUP`
- pilot XI completeness: `PASS`
- full 300-match XI completeness: `NOT_YET_RUN`
- historical lineup PIT timestamp: `UNAVAILABLE`
- label/model stage: `NOT_ENABLED`

在完整300场零标签覆盖审计完成前，不运行正式效果模型，不输出首发结构对xG的增量效果。
