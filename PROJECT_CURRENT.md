# 足球项目当前状态

## 身份

- project_id: `football-project`
- state_version: **59**
- updated_at_utc: `2026-08-16T13:49:39Z`
- updated_by: `GPT-5.6 Sol`
- status_source: `STATE59_PUBLIC_AVAILABILITY_ZERO_LABEL_COVERAGE_PASS`
- status: `WAITING`
- state_log_record_id: `recl9Fysw4nGZioWn`
- predecessor_state: 58

## 正式规则与正式资产

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- formal model/data/config/CURRENT changes in state59: **0**
- formal_weight changes in state59: **0**

state59 只发布新的零标签数据覆盖结论和研究断点；不读取赛果、不训练、不评分、不修改正式资产。

## 上游研究主线

500场 T / Parity / GD 诊断主方向保持：

1. 知道真实 Parity 时性能明显恢复；
2. 知道完整真实 T 时进一步恢复；
3. 现有 X 连 Parity 都预测不好；
4. 第一优先是寻找真正能提升 T 的赛前信息；
5. 第二优先是偶数 T 下解决 `GD=0` vs `GD=±2`；
6. 不恢复 Direct-T 外壳搜索或独立 Parity 分类器作为主路线。

## state58 结果仍有效

multi-OU 单赛事技术可行性保留，但公开免密钥批量 PIT coverage/synchronization gate 为：

**`STOP_DATA/COVERAGE`**

它不是科学 FAIL。市场路线在当前无密钥/无新增付费 Provider 边界下停止。

## state59 新结果：availability 零标签覆盖门 PASS

研究分支：

- branch: `research/xi-availability-public-coverage-r1`
- branch HEAD: `2ffaa97334b139d0bbff9085fec047e3862a5d55`
- GitHub Actions run: `31950837713`
- job: `95173928727`
- run conclusion: `completed/success`
- Artifact: `9264588803`
- Artifact digest: `sha256:53ab1ed11982af43bafd4721055ee4a72e12fbc3b17990290c27678bf093030f`

冻结样本：英超 2026/27 Matchweek 1 共 10 场未来未开赛赛事、20 支球队。

公开官方源：

1. Fantasy Premier League `bootstrap-static` 官方公开 JSON；
2. PremierLeague.com 官方 20 队伤停页；
3. PremierLeague.com 官方停赛页。

实时 runner 结果：

- expected teams: 20
- bound teams: **20**
- expected fixtures: 10
- bound fixtures: **10**
- all sources fetched: **true**
- hard violations: **0**
- source errors: **0**
- verdict: **`PASS_ZERO_LABEL_PUBLIC_AVAILABILITY_COVERAGE`**

FPL 原始 JSON：

- observed/retrieved at: `2026-08-16T13:47:54Z`
- raw SHA-256: `3884edab1ce1b0691910a769c380fe8c0032b60126d2e246f3ca28e00ae86c93`
- current-season team codes 与冻结的 20 队集合完全一致；
- 抓取 587 个球员记录；
- 当前 `status != available` 的球员共 93 人：`i=46 / u=19 / d=25 / s=3`。

另外保存：

- PL injuries raw HTML SHA-256: `bc6c7af5113a68885d540322115dd2f6a2e2c3848fe9e0b9ec173958d273f4f6`
- PL suspensions raw HTML SHA-256: `6de09ebeba3f754fa5ac4d566a1cca0cda6a5b3105b4e8657d122bbe57331d77`

### 结论边界

这个 PASS 只回答：**官方公开 availability 数据能否在赛前以可时间戳、可哈希、无密钥方式覆盖冻结样本。答案是能。**

它绝不等于：

- availability 已证明能提升 T；
- 平局问题已解决；
- 模型效果 PASS；
- confirmed XI 已通过覆盖门。

本轮没有读取任何对应比赛赛果标签。

## 工程发现

仓库已有 `v6_match_context_pit_ledger_v6310.py` 和 `v6_fotmob_match_context_acquire_v6485.py`，其时序、来源、预测 XI 门控逻辑可复用，但都把球队上下文强绑定到已有 market fixture / market-first ledger。

由于市场路线在 state58 已 STOP，这个耦合不能继续作为 availability 的前置条件。state59 的新 collector 因此直接以冻结 fixture manifest 为主键，不依赖市场存在。

## 唯一下一步

进入**多时点赛前上下文冻结门**，仍然保持零标签：

1. availability 层按独立时间点重复冻结，优先 `T-24h` 与 `T-6h`；
2. confirmed XI 单独放在名单公布后的约 `T-75min` 层；
3. confirmed XI 不得回填或覆盖更早 availability freeze；
4. 每次保存 `source_url / observed_at / retrieved_at / raw SHA-256`；
5. 先验证多时点稳定性、增量变化和 confirmed-XI 可采性；
6. 这些 PIT 门完成前继续禁止读取对应赛果标签；
7. 完成后才进入 availability / XI 对 T 的 OOS 增量价值验证。

## 当前禁止事项

- 禁止把 state59 coverage PASS 写成科学 PASS；
- 禁止读取这 10 场未来赛事的赛果标签；
- 禁止 confirmed XI 倒灌到 T-24h/T-6h；
- 禁止重复 multi-OU 探测；
- 禁止合并 research 分支或修改 formal model/data/config/CURRENT；
- 禁止恢复 Direct-T / Parity 算法壳搜索。

## 轻量接续

恢复时只看上一工作对话末尾一小段作为最近语境；权威断点为 Airtable《当前状态》+唯一《执行检查点》+ GitHub main。更早历史只按具体 Rxx/PR/run/Artifact/关键词定向查询。

## Airtable 锚点

- base: `足球项目接续` (`appLXF9IBvSCEUjJV`)
- current_state_record: `recs1pQ1rhuwJQAzE`
- state59 creation log: `recl9Fysw4nGZioWn`
- execution_checkpoint_record: `recIRxK7EIMjJdG4A`
- checkpoint_version: **87**

## 权威优先级

当前用户明确指令 > 唯一正式 CURRENT > 最新 Airtable state/checkpoint > GitHub 当前状态文件 > 历史聊天/旧文件/记忆。
