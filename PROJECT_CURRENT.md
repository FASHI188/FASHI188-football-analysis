# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 47
- updated_at_utc: 2026-08-14T02:03:00Z
- updated_by: GPT-5.6 Sol
- status_source: `GITHUB_APPS_INTEGRATION_CODECOV_ACTIVE_DEEPSOURCE_SONAR_SERVICE_ACTIVATION_PENDING`
- status: WAITING
- state_log_record_id: `recM2AreYV6vPfHxQ`
- project_current_sha256: `RECORDED_IN_AIRTABLE`

## 正式规则状态

- sole_CURRENT: `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`
- CURRENT_version: V5.2.0
- CURRENT_count: 1
- old_version_execution_weight: 0
- formal model/data/config/CURRENT changes in state47: 0
- formal_weight for all research below: 0

## state47当前工程治理任务：GitHub Apps接入

### 已验证可用

- ChatGPT Codex连接器：仓库读取、写入、PR、Actions、Artifact链已实测可用。
- Mend Renovate：已创建Dependency Dashboard #188及依赖更新PR #187，真实工作。
- GitGuardian：GitHub已为其创建Check Suite并能接收push/PR事件；当前未发现secret告警。
- Codecov：已从“仅安装App”推进为真实coverage上传与Check Run。

### Codecov仓库端修复

- governance branch: `chore/github-apps-integration-r1`
- exact_head: `07dced6e25a4e8e5af025fb9388936c44f4f5899`
- Draft PR: `#192` / Open / Draft / Unmerged
- changed file: `.github/workflows/football-codecov-coverage.yml`
- workflow使用GitHub OIDC：`contents: read` + `id-token: write`
- repository Secret新增: 0
- `CODECOV_TOKEN`新增: 0
- Actions run: `31762424361`
- Actions job: `94651393044`
- coverage生成: success
- Codecov OIDC upload: success
- Codecov Check Run: `94651474018` / `codecov/patch` / success
- repository-integrity: success
- changed-file quality/security guard: success

### DeepSource

- 仓库默认分支已存在 `.deepsource.toml`，Python analyzer已配置。
- GitHub App能收到仓库事件并创建Check Suite。
- 当前没有实际Check Run；官方配置要求仓库在DeepSource服务端启用Code Review/analysis。
- 裁决：仓库端配置不再追加冗余scanner；等待DeepSource服务端激活。

### SonarQubeCloud

- 仓库默认分支已存在 `.sonarcloud.properties`，已限定source/test范围。
- GitHub App能收到仓库事件并创建Check Suite。
- 当前没有实际Check Run；Automatic Analysis需要先在SonarQubeCloud导入GitHub仓库并启用。
- 裁决：不创建需要`SONAR_TOKEN`的冗余CI链；优先使用官方Automatic Analysis，等待服务端项目激活。

## 当前研究分支与科学状态保持不变

- PIT research branch: `research/draw-pit-availability-zero-label-20260814`
- latest persisted research HEAD: `2091fbd1e67bc581bf77525b076d3966a8adabef`
- prior draw branch: `research/draw-subtype-mechanism-300-20260813`
- prior research HEAD: `b80cb24edc73ba7aa80d4a6c97e3207d92b32cac`
- Draft PR #191 remains Open / Draft / Unmerged / formal_weight=0

## 必须保留的既有正证据

### R24成熟历史概率确认

- fresh300 / history_n=30,395
- HDA LL delta -0.0088098
- Draw AUC delta +0.0293944
- bootstrap90 LL delta [-0.0173351,-0.0015384]
- probability gate PASS
- Top-1 Draw仍未解决

### R35-R38 Direct-T -> 条件D=0结构链

固定结构：直接预测 P(T=0,1,2,3,4,5,6,7+)，再预测条件 P(D=0|T,X)，最后自然汇总平局概率。

- R35 fresh300: core PASS；LL -0.0044166；AUC +0.0148452；bootstrap90 [-0.0085493,-0.0019548]
- R36 fresh300: LL -0.0041018；AUC +0.0028750；CI跨0
- R37 fresh300: LL -0.0015684；AUC +0.0020704；CI跨0
- R38 fresh1000: LL -0.0017259；AUC +0.0115026；CI跨0
- R35-R38 pooled 1900 diagnostic: LL -0.0025010；AUC +0.0092975；Brier -0.0016548；date-block bootstrap90 [-0.0044489,-0.0005596]
- pooled结果仅为诊断证据，不是预注册晋级门。

## 必须保留的既有反证

- R27B-R33 Top-1/选择性平局执行连续fresh验证未复制。
- R34 32-book盘口微观结构 fresh300 明确FAIL：LL +0.0078691；AUC -0.0351843；bootstrap90 [0.0004084,0.0168185]。
- R39 T-conditioned high-confidence selector fresh1000：固定 qD/market_pD Top30，仅7中；precision23.33%；recall2.78%；整体准确率-0.80pp；同窗概率层也轻微退化。R39 FAIL并封存。

## R40/R41 PIT伤停与可用性研究

### R40零标签PIT可行性

- 数据源：公开FPL历史Git快照。
- 2020/21-2024/25 共恢复 1,738 场真实赛前PIT可用比赛，五季均 >=150。
- 零标签审计阶段目标比分/结果标签读取=0。
- zero-label workflow run: `31726018581`，success。
- Artifact: `9191280711`。
- manifest SHA256: `c1ad9abb3dc91af82f3e4c39bad530d9ffff8a5901ad8040c6c4bb0c2f936c26`。

### R40静态伤停存量残差

结论：明确FAIL。

- 619测试行。
- HDA LogLoss delta +0.030129。
- Draw AUC delta -0.050148。
- bootstrap90 LL delta [+0.016990,+0.043389]。
- 裁决：静态伤停数量/静态攻击缺口不能作为增量平局信号；不得继续在该家族调参。

### R41伤停/可用性“变化冲击”假设

- 可恢复transition PIT约 1,686 场。
- 当前探索诊断使用2023/24+2024/25共609条已消费标签；因此不是独立OOS确认。
- 全局HDA LL delta +0.002107。
- Draw AUC delta -0.002534。
- bootstrap门未通过；R41不能作为全局概率层成功。
- 但自然argmax产生25次Top-1 Draw，命中11次：precision 44.0%，recall 8.27%。
- 2023/24：10次Top-1 Draw，5中；2024/25：15次Top-1 Draw，6中。
- 未使用forced Top-k，未做结果后阈值搜索。
- R41 prereg SHA256: `eea489b77ca4c89a9ab44681599c636f5dbc8d4df60040825798670b860fede1`。

## 科学裁决保持不变

`STATIC_AVAILABILITY_FAILS_BUT_AVAILABILITY_CHANGE_MAY_BE_SPARSE_TOP1_GATE_NOT_CONFIRMED`

- R35-R38证明Direct-T -> 条件D=0是目前更有依据的平局概率结构，但Top-1执行仍未解决。
- R40证明静态伤停存量没有增量价值。
- R41首次出现“赛前可用性短期变化可能作为稀疏Top-1触发信号”的新证据：25报11中/44%。
- R41使用的是已消费标签，且全局LL/AUC未改善，因此只能作为新假设，不能写成独立确认、可用模型或正式晋级。
- R41现有609行必须冻结，禁止调阈值、权重、特征或forced Top-k。

## 唯一下一步

当前用户任务优先：

1. 用户在DeepSource服务端为 `FASHI188/FASHI188-football-analysis` 启用Code Review/analysis；
2. 用户在SonarQubeCloud导入该GitHub仓库并启用Automatic Analysis；
3. 完成后使用新commit/PR只读核验两者是否产生真实Check Run；
4. PR #192保持Draft/Open/Unmerged，未经明确授权不得Ready或合并。

若用户之后继续平局研究，仍按state46冻结门执行：第三时间段零标签PIT覆盖门 -> 同一R41候选原样复核，不得回炉609条。

## 完整R45B前向轨保持不变

- branch: `research/r45b-pit-role-availability-zero-label`
- HEAD: `c5625410463436c2d2bfa10b67634d92644dfdc6`
- fully eligible: 2 / 300
- remaining: 298
- independent_oos_authorized: false
- target labels / training / scoring / tuning / Provider / paid Provider / formal_weight: 全部0

## 其他边界

- 不调用付费Provider。
- 正式模型、正式数据、正式config、CURRENT改动=0。
- 本state只处理GitHub Apps工程接入，不改变任何足球科学结论。
- PR #192不得未经用户明确授权合并或转Ready。

## 新对话恢复

START_HERE唯一版 -> ACTIVE_CHECKPOINT -> Airtable执行检查点 -> LAST_HANDOFF -> PROJECT_CURRENT -> Airtable当前状态/绑定维护日志 -> 必要时唯一CURRENT。

恢复时必须看到 state47，并同时保留state46全部科学证据与冻结边界；当前工程任务为GitHub Apps接入，Codecov已验证，DeepSource/Sonar服务端激活待完成。

## Airtable锚点

- base: 足球项目接续
- current_state_record: `recs1pQ1rhuwJQAzE`
- maintenance_log: `recM2AreYV6vPfHxQ`
- execution_checkpoint: `recIRxK7EIMjJdG4A`
- airtable_state_version: 47
