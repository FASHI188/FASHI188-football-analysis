# 足球项目稳定治理规则

> CONTROL_MARKER: GITHUB_FORMAL_GOVERNANCE_ONLY
> FORMAL_MARKER: GITHUB_UNIQUE_CURRENT_REQUIRED
> AUTH_MARKER: CURRENT_USER_COMMAND_REQUIRED
> MIRROR_MARKER: LOCAL_NON_AUTHORITATIVE
> CURRENT_PATH: football-data/governance/CURRENT_唯一正式规则.md

## 1. 权威链

1. 用户当前明确指令决定本回合授权范围。
2. GitHub 实际 repository / branch / exact HEAD / PR / run / Artifact 决定仓库事实。
3. GitHub 工作树内唯一正式 CURRENT 决定正式模型、预测、训练、评分、晋级、formal_weight 和科学硬规则。
4. GitHub 工作树内 GPT 事实核验硬门决定事实表述与证据分类。
5. Airtable、聊天、本机文件和历史报告只作审计线索，不得覆盖 GitHub 当前事实。

冲突时按更高权威层 fail closed。

## 2. GitHub-only 正式来源

GitHub 是足球3唯一正式持久化来源。唯一 CURRENT 路径为：

`football-data/governance/CURRENT_唯一正式规则.md`

工作树内 CURRENT 候选数量必须恰好为 1。缺失、重复、不可读或路径不符时停止。正式治理修改必须产生 GitHub commit SHA 和 exact-HEAD 远端验收；没有二者即视为未落实。

本机 checkout、缓存、下载 Artifact、聊天附件和 Airtable 副本均为非权威副本，不得反向覆盖 GitHub。

## 3. 授权边界

讨论不等于执行。只有用户当前明确指令允许相应副作用。历史聊天、旧日志和先前授权不得自动扩大到标签、Provider、Secret、训练、调参、正式评分、发布、Ready 或合并。

## 4. 正式 CURRENT 门

涉及正式模型、预测、训练、评分、概率、比分、EV、formal_weight、晋级或科学结论时，必须完整读取唯一 CURRENT 和事实核验硬门。

普通工程 Actions 通过不等于科学 PASS；候选覆盖能力不等于准确率提升；普通 GPT 分析不等于足球3引擎输出。

## 5. 写操作安全

1. 核对用户当前授权；
2. 核对目标 GitHub branch / HEAD / path；
3. 使用最小、可幂等写入；
4. 写后立即回读；
5. exact-HEAD 运行远端验收；
6. 超时先核验副作用，禁止盲目重复。

不得用只在本机修改、测试或生成的文件宣称正式完成。

## 6. 动态事实和审计记录

动态事实以实时 GitHub API 为准，不在仓库文件中伪造会漂移的当前运行状态。Airtable仅在用户另行授权时作为审计镜像更新；不同步不影响 GitHub 已发生事实，但必须明确标注未同步。

## 7. 科学与工程隔离

治理收口不得改变正式核心概率、formal_weight、训练样本、标签、Provider 预算或科学结论。远端 CI success 仅证明对应 exact HEAD 的工程合同执行成功。

## 8. R5 边界

PR #334 / R5 保持 Draft、Open、Unmerged；除非用户另行明确授权，不得修改、解锁、Ready 或合并。后续分支可继承其 Git 历史，但不得把继承表述为已合入 main。
