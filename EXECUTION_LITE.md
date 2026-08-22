# 足球项目执行链轻量化协议｜EXECUTION_LITE V4

> CONTROL_MARKER: GITHUB_FORMAL_GOVERNANCE_ONLY
> FORMAL_MARKER: GITHUB_UNIQUE_CURRENT_REQUIRED
> AUTH_MARKER: CURRENT_USER_COMMAND_REQUIRED
> MIRROR_MARKER: LOCAL_NON_AUTHORITATIVE
> CURRENT_PATH: football-data/governance/CURRENT_唯一正式规则.md

本文件治理执行负载、副作用安全和中断后的实时复核，不保存会漂移的运行状态。

## 1. 执行前

- 当前用户指令决定本回合允许的副作用。
- GitHub repository / branch / exact HEAD 是正式施工锚点。
- 正式科学任务必须读取 GitHub 唯一 CURRENT 和事实核验硬门。
- 讨论不等于执行；没有明确执行授权时只读或回答。

## 2. GitHub-only 写入

项目正式修改直接写入目标 GitHub 分支。本机仅可作临时只读检查，不得作为正式成果、激活源或验收源。

每项写入必须给出 commit SHA、changed files 和 exact-HEAD 远端验收。无远端验收不得宣布竣工。

## 3. 精确读取

已知 path、branch/HEAD、PR、run/job 或 Artifact 时直接精确读取。只有全量审计确有必要时才扩大范围。大计算放入 GitHub Actions 或专门执行服务，聊天仅保存证据锚点。

## 4. 失败与重试

调用超时或返回不明时，先回读 GitHub 判断副作用是否已经发生。已发生不得重复；无法判断则停止。不得退回本机施工绕过 GitHub 权限或网络问题。

## 5. 状态和镜像

GitHub 实时 API 决定当前仓库事实。Airtable只在另行授权时作为审计镜像更新；本机日志和聊天记录永远非权威。

## 6. 权限边界

普通“继续”不自动获得付费 Provider、Secret、新标签、训练、调参、正式评分、晋级、发布、PR Ready 或合并权限。工程绿灯不得表述为科学 PASS。
