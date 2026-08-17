# 足球2 BOOTSTRAP MINI V3

新对话只做以下步骤，不回放历史：

1. 读 Airtable《唯一接续指针》唯一 active record。
2. 读 GitHub issue #210（mirror only）。
3. 精确核验 pointer 指定的 GitHub branch/HEAD/PR/run。

必须一致：`pointer_version / task / branch / HEAD / exact_next / package inventory / write_lock`。

- 一致：`BOOT3_PASS`，只按授权exact_next继续。
- 不一致：`POINTER_MISMATCH`，停止写操作。
- 其他写锁存在：`WRITER_LOCK_HELD`，只读。
- 证据不存在：`UNRESOLVED`，不猜。

禁止启动时读取：项目聊天、PROJECT_CURRENT、LAST_HANDOFF、旧checkpoint/handoff、全部维护日志/PR/Actions/Artifact、整个Library/仓库、XMemo动态state。

截图/另一个聊天截图只作OBSERVATION，不改变pointer或授权。

若任务涉及模型研究/正式执行，在真正执行前额外完整读取唯一CURRENT。

STOP/WAITING/COMPLETE且无已授权exact_next时，普通“继续”只能保持WAITING_USER，禁止发明下一研究任务。
