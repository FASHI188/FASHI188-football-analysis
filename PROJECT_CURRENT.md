# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 51
- updated_at_utc: 2026-08-14T09:20:00Z
- updated_by: GPT-5.6 Sol
- status_source: `STATE51_R54_PUBLISHED_STATE_SYNC_REPAIRED`
- status: WAITING
- state_log_record_id: `recrFkhQsfbpetHcw`

## 正式规则状态

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0
- formal model/data/config/CURRENT changes in state51: 0
- all research below remains formal_weight=0

## 本次state51修复对象

第二次发现“高频执行状态已经前进、正式接续状态仍停在旧版本”的接续失步。

修复前真实状态：

- GitHub `PROJECT_CURRENT.md` / `LAST_HANDOFF.md`: state47；
- Airtable《当前状态》: state47；
- Airtable《当前状态》保存的 checkpoint_version: 21；
- 唯一激活《执行检查点》实际 checkpoint_version: 27；
- 维护日志已经完成 R51、R52、R53、R54。

根因不是CURRENT冲突，也不是研究结果损坏，而是：研究原子步骤可以连续推进；“实质科学结论必须触发新的state publication”过去只有纪律要求，没有下一研究步骤启动前的强制一致性门。因此维护日志/检查点前进，而 `PROJECT_CURRENT` / `LAST_HANDOFF` / Airtable《当前状态》滞后。

state51已把该缺口升级为硬门：任何实质科学结论产生后，下一研究原子步骤启动前必须先完成新的state_version双边发布；否则 `BLOCKED_STATE_PUBLICATION_LAG`。

## 当前科学主链

### 已保留正证据：R35-R38 Direct-T -> 条件D=0

固定结构：直接预测 `P(T=0,1,2,3,4,5,6,7+)`，再预测 `P(D=0|T,X)`，最终自然汇总平局概率。

- R35 fresh300: HDA LL delta -0.0044166；Draw AUC +0.0148452；bootstrap90 [-0.0085493,-0.0019548]。
- R36 fresh300: LL -0.0041018；AUC +0.0028750；CI跨0。
- R37 fresh300: LL -0.0015684；AUC +0.0020704；CI跨0。
- R38 fresh1000: LL -0.0017259；AUC +0.0115026；CI跨0。
- pooled 1900 diagnostic: LL -0.0025010；AUC +0.0092975；Brier -0.0016548；date-block bootstrap90 [-0.0044489,-0.0005596]。

解释：Direct-T→条件D=0仍是当前唯一具有较稳定概率层正证据的平局结构，但自然Top-1平局尚未解决。

### R50

直接HDA可用性变化三种结构全部FAIL。PIT可用性变化不应继续作为顶层1X2残差或直接平局专家；若继续使用，更合理的路径是先影响比赛开放度/总进球，再影响条件D|T。

### R51

2021/22-2024/25已消费历史滚动开发。候选A仅在HDA/Draw proper score上小幅改善，但Direct-T八档LogLoss恶化、Top-1平局10报2中，未过门。R51 FAIL；2025/26确认窗未打开。

### R52：瓶颈定位

- 同一历史包时间前推300场；Direct-T八档 LL=1.86844，优于历史边际1.96593。
- HDA LL 0.89414 -> 0.88903，delta -0.00510。
- Draw Brier -0.001275；AUC +0.003654；日期块bootstrap90 [-0.01066,-0.00032]。
- 自然Top-1平局仍为0。
- 仅用于定位的真实T Oracle：自然报平62，31中，precision 50%，recall 77.5%；总体1X2 accuracy 59.0% -> 62.67%。

裁决：主要瓶颈收敛到赛前 `P(T=0..7+)` 的质量与锐度，不应回到forced draw或平局阈值。

### R53：1X2开放度轴

- 3×300，共900场/247平。
- T LL 1.842193 -> 1.838009，delta -0.004184。
- Draw AUC 0.556178 -> 0.571600。
- HDA LL 0.998384 -> 0.998232，delta -0.000152。
- 自然Top-1平局仅1场且0中；bootstrap未形成硬确认。
- Oracle真实T：自然报平305、189中，precision61.97%、recall76.52%、overall HDA accuracy62.11%。

裁决：1X2开放度轴存在排序信号，但同源1X2信息不够把P(T)锐化到执行层。

### R54：同包Direct-T残差叠加最终反证

- branch: `research/draw-r54-direct-t-rolling900-20260814`
- result commit: `5654a015746e06e33580299052949132db34c7d9`
- sample hash: `d23cc3fe92d8461490d63b1402a8a7ac15ceea2b2f316df390d4543a260ef61b`
- 900场 / 242平。
- candidate T LL 1.832234 vs old 1.830746：更差 +0.001488。
- HDA LL 0.989507 vs market 0.987263：更差 +0.002244。
- Draw AUC 0.566204 vs market 0.581970：更差 -0.015766。
- 自然Top-1平局7报2中，precision28.57%、recall0.83%。
- HDA LL bootstrap90% candidate-market [0.000982,0.004186]：明确有害。
- Oracle真实T仅用于定位：自然报平305、193中，precision63.28%、recall79.75%、overall HDA accuracy63.44%。

裁决：`FAIL_R54_ARCHIVE_ONLY_NO_BREAKTHROUGH_DO_NOT_CONFIRM`。

**核心结论：同一1X2轨迹包 + 球队/联赛历史不能提供足够独立的P(T)信息；条件D|T并非当前首要瓶颈。真正缺的是独立赛前总进球/机会量信息。**

## 当前唯一研究下一步

R55尚未开始。

优先结构：

1. 冻结R54旧Direct-T与R53开放度轴；
2. 只新增与1X2正交的赛前总进球信息，第一优先为真实独立OU市场；
3. 首先研究去水后的OU2.5水平、OU开收变化、庄家分歧以及 `OU信息 - 1X2隐含开放度` 的 Total Surprise / residual；
4. OU不得从零人工制造0-7+分布，只能作为已有Direct-T prior的残差/约束信息；
5. 若有多线OU（1.5/2.0/2.25/2.5/2.75/3.0/3.5），优先用于约束总进球CDF；
6. 第二信息族才是严格赛前PIT射门/射正/机会质量/xG等机会量；
7. 仍使用互斥时间前推/滚动窗口验证，禁止forced draw、Top-k、结果后阈值搜索。

## 当前禁止事项

- 不在R50/R51/R53/R54同窗继续调平局阈值、forced draw、Top-k、class weight或人工1-1奖励；
- 不继续从同一1X2轨迹包堆更复杂分类器；
- 不把Oracle指标当预测结果或晋级证据；
- 不读取新的2025/26确认标签，除非后续另有合法独立OOS授权；
- 不调用付费Provider；
- 不修改正式模型、正式数据、正式config或CURRENT；
- GitHub Apps的DeepSource/Sonar治理任务目前暂停，不作为足球研究阻塞；PR #192继续Draft/Open/Unmerged，未经授权不得合并或Ready。

## state publication防复发门

启动任何新的实质研究原子步骤前，必须同时满足：

- `PROJECT_CURRENT.state_version == LAST_HANDOFF.state_version == Airtable当前状态.state_version == 激活检查点.state_version`；
- Airtable当前状态保存的 `执行检查点版本 == 激活检查点.checkpoint_version`；
- 当前状态绑定维护日志必须对应当前state_version；
- 三份GitHub接续文件SHA与Airtable记录一致；
- 如果上一原子步骤产生新的PASS/FAIL/STOP/封存/瓶颈变化/唯一下一方向变化，则必须先完成state publication，禁止直接启动下一研究步骤。

不满足则停止为 `BLOCKED_STATE_PUBLICATION_LAG`。

## Airtable锚点

- base: `足球项目接续`
- current_state_record: `recs1pQ1rhuwJQAzE`
- state51 maintenance_log: `recrFkhQsfbpetHcw`
- execution_checkpoint: `recIRxK7EIMjJdG4A`

## 权威优先级

当前用户明确指令 > 唯一正式CURRENT > ACTIVE_CHECKPOINT实时协议与唯一激活检查点 > LAST_HANDOFF > PROJECT_CURRENT + Airtable当前状态/绑定维护日志 > 历史聊天/旧文件/记忆。
