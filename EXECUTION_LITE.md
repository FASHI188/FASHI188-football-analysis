# 足球项目执行链轻量化协议｜EXECUTION_LITE V3

> CONTROL_MARKER: AIRTABLE_CURRENT_STATE_ONLY
> AUTH_MARKER: CURRENT_USER_COMMAND_REQUIRED
> MIRROR_MARKER: NO_DYNAMIC_STATE_MIRRORS

本文件只治理执行负载、副作用安全和断线后的事实核验；不保存动态项目状态，也不建立第二套接续系统。

## 1. 执行前

- 动态施工状态只读 Airtable《当前状态》唯一激活记录。
- 当前用户指令决定本回合允许的副作用范围。
- 讨论不等于执行；没有当前明确执行指令时，只读或回答。
- 正式科学任务另按 `AGENTS.md` 读取唯一正式 CURRENT。

## 2. 精确读取优先

已知 record ID、path、branch/HEAD、PR、run/job、Artifact 或关键词锚点时直接精确读取。只有用户明确要求全量审计，或精确路径不足以回答时，才扩大范围。

大 CSV/JSON、ZIP、大日志、批量计算和可重复实验放到 GitHub Actions、容器或专门执行环境；聊天侧只保留必要结论和证据锚点。

## 3. 写操作

每个写操作必须满足：

1. 当前用户指令覆盖该动作；
2. 写前核验目标当前状态；
3. 最小化写入范围；
4. 写后回读；
5. 失败或超时先查真实副作用，再决定是否重试。

禁止因为“怕忘记”额外创建 checkpoint / handoff / pointer / mirror / 状态注册表。

## 4. 断线与重试

断线后不从旧 checkpoint、handoff、pointer 或历史 next step 猜恢复位置。

重新读取 Airtable《当前状态》，再核验当前指令涉及的 GitHub/Provider 实际对象。若副作用已发生则不得重复；明确未发生且当前授权仍覆盖时才可重试；无法判断则停止并报告歧义。

## 5. 状态记录

普通只读核验和未改变项目状态的操作不制造状态噪声。

只有重要项目状态真实改变时：

- 更新 Airtable《当前状态》唯一激活记录；
- 向《维护日志》追加一条历史证据；
- 不在 GitHub 或其他表同步第二份动态状态。

## 6. 权限边界

普通“继续”不能自动获得付费 Provider、Secret、新盲测标签、正式训练/评分/晋级、CURRENT/模型/权重/config 修改、PR Ready/merge、扩大样本或研究预算等权限。

工程治理不得顺手改科学资产；workflow 绿灯不得表述为科学 PASS。
