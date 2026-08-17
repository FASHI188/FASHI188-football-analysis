# 足球2 规则源与接续事故审计｜2026-08-17

## 结论
问题不是缺规则，而是**规则源过多且都曾承担“当前真值”职责**，导致 split-brain、跨对话随机恢复、并发推进和上下文膨胀。

## 已确认冲突
1. Airtable《唯一接续指针》已到 R8 completed/pointer v5，而《当前状态》、checkpoint147、handoff23和XMemo dynamic state仍停在R8开跑前。
2. GitHub main `PROJECT_CURRENT.md` / `LAST_HANDOFF.md`仍是state62，和当日研究动态状态不同步。
3. File Library同时存在两份都自称“唯一START_HERE”的启动器，分别使用四层恢复和FAST_BOOTSTRAP三读取，权威边界冲突。
4. `AGENTS.md`、`ACTIVE_CHECKPOINT.md`仍把current-state/checkpoint当动态恢复中心，和后来新增的《唯一接续指针》冲突。
5. GitHub issue #210曾被写成“SINGLE SOURCE OF TRUTH”，同时Airtable pointer也被当唯一真源，形成双主。
6. XMemo既保存稳定接续协议又保存动态HEAD/exact_next，容易把旧缓存带入新对话。
7. 用户提供“错误对话截图”时，助手曾把截图里的内容误当当前项目状态继续推理，说明缺少截图隔离规则。
8. 普通“继续”曾被错误解释为授权自行选择新研究路线，说明缺少终态/授权语义硬门。
9. 多对话没有单写者锁，错误对话可以在另一个对话仍活跃时推进pointer/branch/run。
10. 启动/执行曾反复展开旧聊天、大日志、Artifact和connector schema，增加上下文与连接中断风险。

## V3 修复
- 单动态真源：Airtable《唯一接续指针》。
- #210降级为mirror only。
- current-state/checkpoint/handoff/PROJECT_CURRENT/LAST_HANDOFF/XMemo动态状态全部降级为历史/镜像。
- BOOT3固定为pointer→#210→实际GitHub事实。
- 增加单写者`写入锁ID`。
- 增加`授权摘要/执行模式/终态策略/上下文预算`。
- 截图和其他对话截图永远是OBSERVATION，不能改变状态/授权。
- “继续”只恢复已有授权exact_next，不能创造新Rxx或打开标签。
- EXECUTION_LITE V2限制执行块和重读取，并主动提前换对话。
- 所有规则源登记进Airtable《治理规则注册表》，标明ACTIVE/MIRROR/HISTORY/DEPRECATED。

## 不可物理治理的部分
ChatGPT Projects可能由产品层自动注入历史项目上下文。仓库/Airtable规则无法直接改变平台的上下文窗口或自动注入量。V3能做的是：不主动再读旧聊天/旧状态、大工作下放、聊天只保留小回执，并在安全pointer建立后主动换新对话。

## 验收目标
新对话只凭3个小读取应能明确回答：当前任务是什么、谁持写锁、做到哪、唯一下一步是什么、哪些动作未授权；不需要用户重述，也不需要加载旧研究历史。
