# 足球项目稳定治理规则

> CONTROL_MARKER: AIRTABLE_CURRENT_STATE_ONLY
> FORMAL_MARKER: FORMAL_CURRENT_WHEN_REQUIRED
> AUTH_MARKER: CURRENT_USER_COMMAND_REQUIRED
> MIRROR_MARKER: NO_DYNAMIC_STATE_MIRRORS

本文件只定义长期稳定的治理边界，不保存任何动态 Rxx、PR、HEAD、run、Artifact、state_version 或“唯一下一步”。

## 1. 权威链

项目运行时按以下顺序判定：

1. 用户当前明确指令：决定本回合是否允许产生副作用以及允许范围。
2. Airtable《当前状态》唯一激活记录：唯一动态施工状态源。
3. GitHub 实际 branch / HEAD / PR / run / Artifact：只用于核验已经发生的仓库事实。
4. 唯一正式 `CURRENT_唯一正式规则`：只在正式模型、训练、评分、预测、晋级、正式概率/比分/EV、formal_weight 或科学硬规则判断时读取。
5. Airtable《维护日志》、Git 历史、历史 PR/文档/聊天：只作历史证据，不决定当前任务或授权。

任一层发生冲突时，停止猜测并核验更高权威层。

## 2. 唯一动态状态源

动态施工状态只读 Airtable《当前状态》唯一激活记录。

- 不在 GitHub、聊天、Issue、文件或其他 Airtable 表创建第二份动态 current state。
- 不创建或恢复 checkpoint / handoff / pointer / mirror / state registry 作为实时接续链。
- 根目录禁止重新引入旧 `ACTIVE_CHECKPOINT.md`、`PROJECT_CURRENT.md`、`LAST_HANDOFF.md`、`CHATGPT_PROJECT_START_HERE.txt` 或 `HISTORY_ONLY_INDEX.md`。
- 旧《执行检查点》《会话接力》《唯一接续指针》《治理规则注册表》仅为历史证据。

若 Airtable《当前状态》不可读或不存在唯一激活记录，不得猜当前施工位置，也不得产生依赖项目动态状态的副作用。

## 3. 授权边界

讨论不等于执行。

- “看看 / 怎么办 / 能不能 / 下面干啥 / 怎么整 / 到哪了”只允许分析、回答和只读核验。
- 只有用户当前明确说“开始 / 执行 / 去做 / 处理 / 修好 / 合并 / 删除”等对应动作，才在该指令范围内产生副作用。
- 历史聊天、日志、旧 PR、旧任务中的授权不得自动跨时点继承。
- 新研究、打开新标签/数据包、修改正式资产、PR Ready/merge 等仍需当前明确授权。

## 4. 正式 CURRENT 门

普通治理、仓库维护、状态查询和历史取证不需要读取正式 CURRENT。

涉及正式模型、训练、评分、预测、晋级、正式概率/比分/EV、formal_weight、正式 config 或科学硬规则时，必须读取项目作用域内唯一 `CURRENT_唯一正式规则`。

- 数量不等于 1：停止。
- 无法完整读取：停止。
- 不得用 GitHub 历史文件、Airtable 日志或聊天记忆替代正式 CURRENT。
- 不得为了找 CURRENT 扩大到不相关项目或全局旧文件范围。

## 5. GitHub 的职责

GitHub 只承担：代码、数据/研究资产、CI/workflow、审计证据和可复现事实。

GitHub 不承担：动态任务选择、实时接续状态、用户授权镜像或第二份 CURRENT。

核验仓库事实时优先精确读取 branch / HEAD / PR / run / Artifact / path；不要为窄问题默认扫描整仓库、全部 PR 或全部 Actions 历史。

历史治理计划如果已从工作树删除，需要追溯时使用 Git 历史或 Airtable《维护日志》，不要恢复成根目录活动文件。

## 6. 写操作安全

产生 GitHub/Airtable/Provider 等副作用前：

1. 确认用户当前指令明确覆盖该动作；
2. 精确核验目标对象当前状态；
3. 使用尽可能小、可幂等、可核验的写入；
4. 写后立即回读真实结果；
5. 调用超时或结果不明时先核验副作用是否已发生，禁止盲目重复。

只有重要项目状态真实变化时，才更新 Airtable《当前状态》并追加一条《维护日志》。不得为“防忘记”制造新的动态镜像层。

## 7. 科学与工程隔离

纯治理 PR 不得顺手修改模型概率、formal_weight、训练样本、标签、Provider 预算、正式 config、CURRENT 或科学结论。

Actions success 只代表工程执行成功，不等于科学 PASS。科学状态必须按正式 CURRENT 的门槛单独裁决。
