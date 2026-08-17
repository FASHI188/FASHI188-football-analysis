# 足球2 接续与执行入口｜V3

本文件只保存稳定治理规则，不保存动态 Rxx/HEAD/run/Artifact。动态真值只在 Airtable《唯一接续指针》。

## 1. 新对话 BOOT3
收到“继续 / 直接开始 / 接着干 / 到哪了”时，默认只做三项小读取：
1. Airtable《唯一接续指针》唯一 active record；
2. GitHub issue #210（mirror only）；
3. pointer 指定的实际 GitHub branch/HEAD/PR/run。

核验 `pointer_version / task / branch / HEAD / exact_next / inventory / 写入锁ID`。一致才 `BOOT3_PASS`；不一致 `POINTER_MISMATCH` 并停止写操作。

正常启动禁止：搜索项目聊天、读 PROJECT_CURRENT/LAST_HANDOFF、读旧checkpoint/handoff、列全部维护日志/PR/Actions/Artifact、遍历 File Library/仓库、读取 XMemo 动态缓存。

## 2. 权威顺序
用户当前明确指令 > 唯一正式 CURRENT（正式规则事项） > Airtable《唯一接续指针》 > GitHub实际事实 > #210镜像 > 治理规则注册表 > 历史层。

截图、另一聊天截图、旧回复、项目记忆仅为观察/历史证据，绝不自动成为当前状态或授权。找不到就 `UNRESOLVED`，不得猜。

## 3. CURRENT 硬门
若 exact_next 涉及模型研究、训练、评分、晋级、正式预测/概率/比分/EV或CURRENT修改，在实际科学/正式执行前必须完整读取 File Library 中唯一文件名含 `CURRENT_唯一正式规则` 的文件；数量!=1或不可完整读取立即停止。BOOT3不能绕过CURRENT。

## 4. 单写者锁
任何 GitHub/Airtable写入、workflow触发、标签打开、Provider调用或其他副作用前，pointer必须有 `写入锁ID`。非锁持有者只能只读并返回 `WRITER_LOCK_HELD`。每个副作用前回读pointer确认 version+lock+target未漂移；禁止抢锁、双写、并发创建第二执行链。

## 5. “继续”的语义
“继续/直接开始/接着干”只恢复 pointer 已记录且仍获授权的 exact_next，不扩大权限。
绝不自动授权：新Rxx/新研究方向、新标签/B05+、扩大样本或预算、Provider/Secret、正式训练/晋级、formal model/data/config/CURRENT修改、PR Ready/merge、删除历史证据。
STOP/WAITING/COMPLETE 且没有已授权 exact_next 时保持 `WAITING_USER`，不得自行挑下一刀。

## 6. 原子状态事务
每个实质变化：回读pointer+锁 → 一个授权原子动作 → 核验副作用 → pointer_version+1 → 同步#210 → 核实际GitHub事实 → 全一致后再向用户汇报。失败保持锁并BLOCKED，禁止带着分裂状态进入下一科学步骤。

## 7. 动态状态单一化
- 《唯一接续指针》：唯一实时动态真源；
- #210：mirror only；
- 《当前状态》：慢变正式/main治理历史；
- 《执行检查点》《会话接力》：history only；
- PROJECT_CURRENT/LAST_HANDOFF：main历史快照；
- XMemo：稳定协议镜像，不保存动态HEAD/run/exact_next；
- 旧START_HERE：deprecated。

## 8. 上下文与长任务
执行负载遵守 `EXECUTION_LITE.md` V2。新对话不回放历史；大日志/大Artifact/批量数据下放Actions/容器；聊天只保留结果、证据锚点、阻塞、exact_next。达到主动换对话门时先安全更新pointer，再提示新对话只需“继续”。

详细规则见 `governance/CONTINUITY_PROTOCOL_V3.md` 和 `governance/BOOTSTRAP_MINI_V3.md`。
