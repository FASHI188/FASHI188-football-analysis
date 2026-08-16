# 足球项目最后接力点

> 最近语境只看上一工作对话末尾；真正恢复点以 Airtable《当前状态》+唯一《执行检查点》为准。禁止扫描整个旧项目聊天。

## 接力身份

- project_id: `football-project`
- handoff_version: **29**
- state_version: **58**
- checkpoint_version: **84**
- updated_at_utc: `2026-08-16T13:39:55Z`
- status: `WAITING`
- state_log_record_id: `recnkUIaxylA6KRiZ`

## 已完成

本轮完成 multi-OU 批量未来赛事零标签 coverage/synchronization gate。

裁决：

**`STOP_DATA/COVERAGE`**

原因不是“盘口不存在”或“市场无效”，而是当前公开免密钥链无法提供可审计的逐场同窗 `Match Odds + OU1.5/2.5/3.5/4.5 + back/lay + matched + observed_at + source/hash`。

关键边界：

- 搜索缓存页不能冒充实时 PIT first_observed_at；
- 当前未来赛事 Exchange 页面未稳定逐场暴露四档核心 OU；
- 官方 Exchange API 需要账号、App Key、session token，Live Key另涉及激活费用；
- 本轮全程未读取赛果标签、未训练、未评分；
- 因此按 CURRENT 在 coverage gate 停止，属于数据可用性 STOP，不是科学 FAIL。

## 当前唯一下一步

切换到：

**observed_at XI / injury / availability 公开源零标签 coverage gate**

具体先做：

1. 从未来未开赛赛事中冻结样本；
2. 查官方联赛/俱乐部/球队一手来源；
3. 验证赛前 XI、伤停、停赛、availability 是否能在比赛前稳定获得；
4. 保存 source URL、published_at/first_observed_at、retrieved_at、正文或原始响应 hash；
5. 只做覆盖与 PIT 审计，不读赛果；
6. coverage 通过后，才允许进入 forward 冻结与后续增量价值验证。

## 禁止重复

- 不再重复 multi-OU 单赛事技术探测；
- 不用搜索缓存拼出“伪同步”；
- 不请求/使用付费 Live API；
- 不读赛果标签；
- 不恢复 Direct-T / Parity 模型壳搜索；
- 不修改 formal model/data/config/CURRENT。

## 恢复方式

新聊天用户只需说“继续”。

恢复顺序：

1. 上一工作对话最后一小段（若可直接获得，仅作最近语境）；
2. Airtable《当前状态》；
3. 精确读取唯一《执行检查点》；
4. 核验 GitHub main；
5. 从 XI / injury / availability 零标签覆盖门继续。

更早历史仅按需查维护日志，不全量回放旧聊天。
