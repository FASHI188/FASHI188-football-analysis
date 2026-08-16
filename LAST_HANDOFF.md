# 足球项目最后接力点

> 最近语境只看上一工作对话末尾；真正恢复点以 Airtable《当前状态》+唯一《执行检查点》为准。禁止扫描整个旧项目聊天。

## 接力身份

- project_id: `football-project`
- handoff_version: **30**
- state_version: **59**
- checkpoint_version: **87**
- updated_at_utc: `2026-08-16T13:49:39Z`
- status: `WAITING`
- state_log_record_id: `recl9Fysw4nGZioWn`

## 最新完成

### 1. multi-OU

公开免密钥批量 PIT coverage/synchronization gate：

**`STOP_DATA/COVERAGE`**

不是科学 FAIL，不再重复该路线。

### 2. availability

官方公开 availability 零标签 coverage gate：

**`PASS_ZERO_LABEL_PUBLIC_AVAILABILITY_COVERAGE`**

真实锚点：

- branch: `research/xi-availability-public-coverage-r1`
- HEAD: `2ffaa97334b139d0bbff9085fec047e3862a5d55`
- run: `31950837713` completed/success
- job: `95173928727`
- Artifact: `9264588803`
- Artifact SHA-256: `53ab1ed11982af43bafd4721055ee4a72e12fbc3b17990290c27678bf093030f`
- FPL raw SHA-256: `3884edab1ce1b0691910a769c380fe8c0032b60126d2e246f3ca28e00ae86c93`
- 20/20 teams bound
- 10/10 future MW1 fixtures bound
- 3/3 official sources fetched
- hard violations 0
- source errors 0
- 587 player rows; 93 non-available statuses (`i=46/u=19/d=25/s=3`)

本轮没有读取赛果标签，没有训练或评分。

## 结论边界

availability 的 PASS 只证明：官方公开源可以赛前无密钥、带时间戳和 raw hash 覆盖冻结样本。

它不证明 availability 能提升 T，也不证明 confirmed XI 已可用，更不代表平局问题已解决。

## 工程结论

旧 PIT 阵容/availability 代码的语义门控可复用，但强绑定 market-first fixture。市场路线已停止，因此新的 availability collector 直接使用冻结 fixture manifest，不再依赖市场存在。

## 唯一下一步

**多时点赛前上下文冻结门：**

1. availability：T-24h / T-6h 重复冻结；
2. confirmed XI：名单公布后约 T-75min 独立冻结；
3. 每次保存 source URL、observed_at、retrieved_at、raw SHA-256；
4. confirmed XI 禁止倒灌更早 freeze；
5. 先验证时序稳定性和 confirmed-XI 可采性；
6. PIT 门完成前继续不读赛果；
7. 然后才做 availability / XI 对 T 的 OOS 增量价值验证。

## 禁止重复/越界

- 不把 coverage PASS 写成科学 PASS；
- 不读未来首轮比赛结果；
- 不回到 multi-OU；
- 不让 confirmed XI 回填早期 availability；
- 不合并 research 分支；
- 不修改 formal model/data/config/CURRENT；
- 不恢复 Direct-T / Parity 外壳搜索。

## 新聊天收到“继续”时

恢复顺序：上一工作对话末尾小段（若可得）→ Airtable 当前状态 → 精确检查点 → GitHub main。然后从多时点 availability / confirmed XI 零标签冻结门继续。
