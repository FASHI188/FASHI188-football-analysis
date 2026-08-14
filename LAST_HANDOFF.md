# 足球项目最后对话接力点

> 实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 20
- state_version: 52
- updated_at_utc: 2026-08-14T09:31:00Z
- status: WAITING

## 用户最后指令

用户要求先治理，然后继续使用本轮上传 `archive (1)(1).zip` 找300场试试。

## 本轮实际执行

1. 启动前重新核验state51/checkpoint28一致性，GitHub main仍为 `28980cdcb51993d6a4177153635f6eb2e8421211`，通过研究启动硬门。
2. 启动R55原子步骤，checkpoint_version升级为29，并同步Airtable《当前状态》。
3. 冻结样本前只审计ZIP文件名、CSV表头和明确排除赛果的身份/1X2列。
4. 发现archive内OU/Over/Under/totals字段为0，只有closing 1X2与32家×72网格1X2轨迹。
5. 仍按零标签规则冻结一批300场，用于证明样本身份与数据门，不读取赛果。
6. 因独立OU数据门失败，停止模型拟合和评分，未重复R53/R54同源1X2实验。
7. 按state51新增硬门，立即发布state52，不继续下一研究步骤。

## 300场冻结身份

- source archive SHA-256:
  `8bb898c3c067dfca4c4e50f7e0fb3f01104b52a1d748c27ea7973ec96b36cab1`
- source file: `odds_series_b.csv.gz`
- selection:
  - date >= 2016-04-01
  - grid71至少8家完整H/D/A
  - 按match_date/match_time/match_id排序取前300
- sample count: 300
- time window: `2016-04-01 00:30:00` 至 `2016-04-02 14:00:00`
- league count: 125
- sample SHA-256:
  `0954c122748d93576f96bbd3b526c4b4ecff7fec3ecd1f26ee3538bdb1a91ce3`
- zero-label manifest SHA-256:
  `ed7b1a6b8be7cabb58b2dd86799df31d1f11eeab0e575f19a42eaed4f445e784`

## 标签与模型状态

冻结前后本轮均未打开赛果用于R55模型试验：

- score/result labels read for R55 scoring: 0
- model fit: 0
- scoring: 0
- forced draw / Top-k: 0
- threshold search: 0
- formal_weight: 0

## 固定裁决

`R55_ARCHIVE_DATA_GATE_FAIL_NO_OU`

这不代表“OU模型失败”，只代表：

**当前这个archive根本没有OU数据，因此不能检验OU residual / Total Surprise。**

如果强行继续，只能再次使用1X2轨迹信息，等价于重复已经由R53/R54证明边际不足或有害的同源路线，所以本轮选择停止而不是制造结果。

## 当前科学接力点

- Direct-T -> 条件D=0主链保留；
- 主要瓶颈仍是赛前P(T)质量与锐度；
- 同源1X2信息族已基本榨干；
- 下一条有意义的新信息必须是真实独立OU/Total，或严格赛前机会量/xG等PIT信息。

## 唯一下一步

若继续R55，先取得真实赛前OU/Total数据并与历史比赛严格匹配，然后重新执行：
`OU residual / Total Surprise -> Direct-T prior修正 -> 条件D|T -> 自然H/D/A`

在获得OU前，不打开这300场标签去做同源替代实验。

## 禁止事项

- 禁止把1X2开放度或轨迹冒充OU；
- 禁止为了出结果在这300场上结果后调阈值、forced draw、Top-k或class weight；
- 禁止读取新的2025/26确认标签；
- 禁止付费Provider、正式晋级、CURRENT或正式资产修改。

## Airtable恢复锚点

- current_state: `recs1pQ1rhuwJQAzE`
- state52 maintenance log: `rectVxackbVvNDUxq`
- execution checkpoint: `recIRxK7EIMjJdG4A`
- checkpoint_version: 29

## 新对话恢复硬门

启动研究前必须确认：

`PROJECT_CURRENT.state_version = LAST_HANDOFF.state_version = Airtable当前状态.state_version = 激活检查点.state_version = 52`

并且：

`Airtable当前状态.执行检查点版本 = 激活检查点.checkpoint_version = 29`

如不一致，不得研究，固定停止为 `BLOCKED_STATE_PUBLICATION_LAG`。
