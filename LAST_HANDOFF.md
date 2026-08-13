# 足球项目最后对话接力点

> 实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 15
- state_version: 43
- updated_at_utc: 2026-08-13T16:22:00Z
- status: WAITING

## 当前真实研究断点

- 用户上传历史数据包：`archive (1)(1).zip` / `closing_odds.csv.gz`，约479,440场。
- 研究分支：`research/draw-subtype-mechanism-300-20260813`
- HEAD：`c12c14b8f65b716aadbc650f90d0c787ca0eb009`
- Draft PR：#191，Open / Draft / 未合并，formal_weight=0。
- 正式模型、正式数据、正式config、CURRENT改动均为0。

## R23 / R24必须同时恢复

### R23：早期历史严格确认 FAIL

- history_n=8,728。
- 使用全新早期历史窗口，严格预注册。
- HDA LogLoss delta +0.0030867。
- Draw AUC delta -0.00873。
- CI跨0。
- 该失败结果必须保留，禁止回炉R23调参或删除失败证据。

### R24：成熟历史盲确认 PASS

- history_n=30,395。
- 排除所有既往 `*_predictions.csv` 已出现的match_id。
- 从4,060个未评分候选中label-free哈希锁定全新300场。
- R21的68特征及参数完全冻结。
- HDA LogLoss 0.9624433→0.9536335，delta -0.0088098。
- Draw LogLoss 0.5189301→0.5101202。
- Draw AUC 0.5653028→0.5946972。
- 90%日期块bootstrap CI [-0.0173351,-0.0015384]。
- prereg core gate PASS。
- Top-1仍未解决：candidate argmax仅1场平局且未命中。

## 当前裁决

- R24确认了成熟历史条件下的概率质量增量，但没有解决Top-1平局执行。
- 不得把R24写成“平局问题已经解决”或正式晋级。
- R21/R24概率候选保持冻结。
- R23/R24都不得重跑，也不得在已查看300场上调阈值。
- 当前剩余主问题仅为Top-1平局执行层，但是否继续研究由当前对话用户指令决定。

## 当前用户指令边界

- 当前这次任务只治理跨对话接续断层。
- 不继续研究场次，因为原对话正在研究。
- 不启动新的300场、不读取新标签、不训练、不评分、不调参。
- 不修改PR #191研究内容。
- 不修改正式模型、formal_weight、正式config或CURRENT。

## 新对话必须如何接

1. 读取唯一START_HERE。
2. 读取ACTIVE_CHECKPOINT协议及Airtable唯一激活执行检查点。
3. 读取本文件，确认 `state_version=43`。
4. 读取 `PROJECT_CURRENT.md`，必须同为 `state_version=43`。
5. 读取Airtable《当前状态》，必须同为state43，并核验其绑定维护日志 `recGS4AUjdYQwKwX6`。
6. 读取唯一CURRENT V5.2.0。
7. 若用户说继续研究，则从R24之后的Top-1执行层继续；若用户只要求治理，则禁止启动研究。
8. 任何一处再次出现state_version不一致，先治理一致性，禁止自行选版本。

## 权威优先级

用户当前明确指令 > 唯一正式CURRENT（正式规则事项） > ACTIVE_CHECKPOINT实时断点 > LAST_HANDOFF > PROJECT_CURRENT + Airtable当前状态/绑定维护日志 > 历史聊天/旧文件/记忆。
