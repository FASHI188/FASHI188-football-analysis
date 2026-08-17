# 足球项目稳定接续规则

> CONTROL_MARKER: AIRTABLE_CURRENT_STATE_ONLY
> FORMAL_MARKER: FORMAL_CURRENT_WHEN_REQUIRED

本文件只定义长期稳定的接续边界，不保存任何动态 Rxx、PR、HEAD、run、Artifact、state_version 或“唯一下一步”。

## 1. 唯一实时状态入口

新对话、刷新、断线恢复时，动态施工状态只读取 Airtable《当前状态》唯一激活记录。

- 《当前状态》决定当前阶段、是否正在执行、阻塞、允许事项、禁止事项和下一步。
- Airtable《维护日志》只用于历史取证，不得恢复任务或授予执行权限。
- 旧《执行检查点》《会话接力》《唯一接续指针》《治理规则注册表》以及 GitHub 的旧状态镜像全部是 HISTORY ONLY。
- `PROJECT_CURRENT.md`、`LAST_HANDOFF.md`、`ACTIVE_CHECKPOINT.md` 不再参与启动、恢复、授权或任务选择。

若《当前状态》不可读或不存在唯一激活记录：只能做不依赖项目动态状态的一般解释；不得猜当前施工位置，不得产生项目副作用。

## 2. 用户授权边界

恢复上下文不等于执行授权。

- 用户明确说“开始/执行/去做/处理”等，才按当前指令范围产生对应副作用。
- “到哪了/下面干啥/能不能/怎么看”只允许回答、分析或只读核验。
- 当《当前状态》为 STOPPED / WAITING_USER / IDLE 时，普通“继续”不得发明新 Rxx、重启旧实验、打开新标签、创建新研究任务或恢复历史日志中的下一步。
- 历史日志、旧 PR、旧聊天、旧 handoff、旧 checkpoint 中的授权均不能跨时点继承为当前授权。

## 3. 正式 CURRENT 门

涉及正式预测、模型研究、训练、评分、晋级、正式概率/比分/EV、正式模型/权重/config 或 CURRENT 修改时，除《当前状态》外，还必须完整读取 File Library / 项目来源中唯一名称含 `CURRENT_唯一正式规则` 的正式规则。

- 数量不等于 1：停止。
- 无法完整读取：停止。
- GitHub 历史文档、研究 PR、Airtable 日志和聊天记忆都不能替代正式 CURRENT。

普通状态查询、历史证据查询和纯治理工作不需要读取 CURRENT。

## 4. GitHub 的角色

GitHub 保存代码、研究资产、审计证据和历史治理文件，但不再保存第二份动态“当前状态”。

需要核验具体仓库事实时，按当前任务精确读取 branch / HEAD / PR / run / Artifact；GitHub 事实可以证明“发生了什么”，但不能单独决定“现在该执行什么”。

## 5. 历史检索

用户问某个 Rxx、PR、run、Artifact 或旧结论时：

1. 优先按 Rxx/关键词精确查 Airtable《维护日志》；
2. 再按日志中的精确 GitHub 锚点核验证据；
3. 不为回答单个历史问题默认扫描全部聊天、全部日志、全部 PR 或整个仓库。

找不到就明确说找不到，不得补猜编号或进度。

## 6. 写操作安全

产生 GitHub/Airtable 等副作用前：

- 确认用户当前指令明确覆盖该动作；
- 精确核验目标对象当前状态；
- 优先使用可幂等、可核验的最小写入；
- 写后立即回读真实结果；
- 调用超时或结果不明时先查副作用是否已发生，禁止盲目重复。

重要项目状态真正改变时，更新 Airtable《当前状态》并追加一条《维护日志》。不再创建或维护 checkpoint / handoff / pointer 镜像链。

## 7. 事实表述

PR/HEAD、Actions/run/job、Artifact/SHA、训练/评分、标签访问、Provider/Secret、Ready/merge、模型效果等必须区分真实核验、转述和推断。Workflow success 不等于科学 PASS，历史事实不等于当前授权。
