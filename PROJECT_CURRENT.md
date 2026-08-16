# 足球项目当前状态

## 身份

- project_id: `football-project`
- state_version: **58**
- updated_at_utc: `2026-08-16T13:39:55Z`
- updated_by: `GPT-5.6 Sol`
- status_source: `STATE58_MULTI_OU_STOP_DATA_COVERAGE_TO_XI_AVAILABILITY`
- status: `WAITING`
- state_log_record_id: `recnkUIaxylA6KRiZ`
- predecessor_state: 57

## 正式规则与正式资产

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- formal model/data/config/CURRENT changes in state58: **0**
- formal_weight changes in state58: **0**

state58 只发布零标签数据覆盖门的裁决与研究信息源切换；不修改正式模型、正式数据、正式配置或 CURRENT，不启动训练/评分。

## 上游研究结论仍有效

500场 T / Parity / GD 诊断主方向不变：

1. 知道真实 Parity 时性能明显恢复；
2. 知道完整真实 T 时进一步恢复；
3. 现有 X 连 Parity 都预测不好；
4. 第一优先仍是寻找真正能提升 T 的赛前信息；
5. 第二优先仍是偶数 T 下解决 `GD=0` vs `GD=±2`；
6. 不恢复独立 Parity 硬分类或 Direct-T 外壳搜索作为主路线。

## state58 新裁决：multi-OU 批量覆盖门

此前“multi-OU 单赛事公开源技术可行性 PASS”继续保留，但它只证明少量页面可以看到多档总进球市场，不等于批量 PIT 数据可用。

本轮在不读取赛果/标签的前提下，对未来未开赛赛事做批量覆盖与同步性审计，得到：

- 英超 2026/27 首轮未来赛事在 Betfair Exchange 可见 Match Odds；
- Betfair 平台存在 OU1.5 / 2.5 / 3.5 / 4.5 等市场类型；
- 但未来赛事的 Exchange 搜索/事件页没有稳定逐场同时暴露上述四档核心 OU；
- 搜索引擎抓取页存在缓存时间，不能作为可信的 `collector_first_observed_at`，也不能提供当前原始 payload 的 PIT hash；
- Betfair 官方 Exchange API 可以读取市场价格/成交数据，但要求 Betfair 账号、App Key 与 session token；Live Key 还涉及激活费用，不符合当前“公开免密钥、无新增付费 Provider”的研究边界。

因此本轮裁决为：

**`STOP_DATA/COVERAGE`**

这不是模型科学 FAIL，也不是“市场信息无效”。含义仅为：**当前公开免密钥采集链不能通过 CURRENT 要求的零标签批量 PIT coverage/synchronization gate。**

按 CURRENT，在覆盖不足时必须在读取标签、训练或评分前停止；本轮已遵守。

## 当前研究方向

市场源路线在当前约束下停止继续扩展，不再重复单赛事 multi-OU 技术探测。

下一信息源切换为：

**observed_at XI / injury / availability**

目标仍然是寻找能够解释/预测总进球状态 T 的真实赛前信息，而不是换模型壳。

## 唯一下一步

对未来未开赛赛事做 **XI / 伤停 / availability 公开源零标签覆盖门**：

1. 优先官方联赛、俱乐部、球队新闻/赛前发布会/阵容页等一手来源；
2. 每条信息必须可记录 source URL、发布时间或可信 first_observed_at、retrieved_at 与正文/原始响应 hash；
3. 先只验证覆盖率、时间可用性、同步性与可复现性；
4. 覆盖门通过前禁止读取对应赛果标签、训练或评分；
5. 若该源同样无法达到 PIT 审计要求，再 STOP_DATA/COVERAGE 并转下一信息源，不得用缓存或后见信息填洞。

## 当前禁止事项

- 禁止把 multi-OU `STOP_DATA/COVERAGE` 写成科学 FAIL；
- 禁止用搜索缓存时间冒充采集 first_observed_at；
- 禁止要求或使用付费 Live API；
- 禁止重复已有 multi-OU 单赛事技术可行性探测；
- 禁止恢复 Direct-T / Parity 算法外壳搜索；
- 禁止在新数据源 coverage gate 通过前读取赛果标签；
- 禁止修改 formal model/data/config/CURRENT。

## 轻量接续

恢复时不扫描整个旧项目。上一工作对话末尾仅作为最近语境；权威断点为 Airtable《当前状态》+唯一《执行检查点》+ GitHub main。

更早历史只按具体 Rxx / PR / run / Artifact / 关键词定向查询维护日志或 GitHub 证据。

## Airtable锚点

- base: `足球项目接续` (`appLXF9IBvSCEUjJV`)
- current_state_record: `recs1pQ1rhuwJQAzE`
- state58 creation log: `recnkUIaxylA6KRiZ`
- execution_checkpoint_record: `recIRxK7EIMjJdG4A`
- checkpoint_version: **84**

## 权威优先级

当前用户明确指令 > 唯一正式 CURRENT > 最新 Airtable state/checkpoint > GitHub 当前状态文件 > 历史聊天/旧文件/记忆。
