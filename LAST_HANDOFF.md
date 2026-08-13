# 足球项目最后对话接力点

> 本文件只保存上一条对话末端如何继续。实时原子步骤断点优先读取 `ACTIVE_CHECKPOINT.md` + Airtable《执行检查点》；正式规则仍以唯一 CURRENT 为最高依据。

## 接力身份

- project_id: football-project
- handoff_version: 12
- state_version: 40
- updated_at_utc: 2026-08-13T12:16:30Z
- status: WAITING

## 用户最后明确目标

- 用户纠正研究方向：R45B训练/历史研究先用其下载的数据包，不应主要等未来比赛。
- 用户明确将本轮样本量固定为 **300场**。
- 本轮已完成固定300场历史盘口轨迹开发实验并保存GitHub证据。
- 总进球继续暂停。

## 本轮已完成

1. 读取用户上传 `archive (1)(1).zip`，SHA256=`8bb898c3c067dfca4c4e50f7e0fb3f01104b52a1d748c27ea7973ec96b36cab1`。
2. 确认包内有 `closing_odds.csv.gz`、`odds_series.csv.gz`、`odds_series_b.csv.gz` 及两份match mapping；盘口轨迹表含最终比分标签，但不含完整R45B的预计首发/角色、伤停替代、process/task字段。
3. 固定历史开发候选：`HIST300_DRAW_MASS_LOGIT_OFFSET_ODDS_TRAJECTORY_R1`；不是完整R45B候选。
4. `odds_series.csv.gz` 共31,074场；固定检查点24/48/71，每个时点至少3家完整H/D/A后可用池15,513场。
5. seed=20260813等概率抽300场，抽样不使用赛果值；之后按时间顺序排列，边界119/178/238/300，形成119初始训练 + 59/60/62三OOS折。
6. 使用固定8个盘口轨迹/分歧特征；L2 lambda=1.0；无candidate search、threshold tuning、结果后feature selection。
7. pooled OOS n=181：市场baseline multiclass LL=0.9683259，challenger=1.0028327，delta=+0.0345068（恶化）；90%按日期block bootstrap CI=[+0.0115370,+0.0584115]，整段>0。
8. Draw binary LL同样恶化+0.0345068；Draw AUC从0.576829降到0.497387；accuracy从54.14%降到51.93%。
9. 三折proper-score改善0/3；Top1 Draw从0场增到7场，但只命中1场，precision14.29%、recall2.44%，不能视为进步。
10. 研究分支：`research/r45b-hist300-odds-trajectory-20260813`；HEAD=`3cb6fe9ee859552d1b90d5b3cc01694214344d21`。
11. GitHub已保存脚本、结果JSON、181场逐场OOS预测：
   - `football-data/research/r45b_hist300_odds_trajectory_r1.py`
   - `football-data/research/r45b_hist300_odds_trajectory_result.json`
   - `football-data/research/r45b_hist300_odds_trajectory_oos181.csv`
12. 该candidate裁决：`FAIL / SEALED_ON_THIS_300`，formal_weight=0。

## 关键解释

- 用户的数据包确实可以用于历史训练/开发；之前“只能等未来300场”是把训练开发和独立前向验证混在了一起。
- 但该数据包当前只证明可以研究“盘口轨迹→平局概率”，不能冒充完整R45B，因为缺少预计首发/角色、伤停替代、过程能力和任务状态等字段。
- 本次失败只封存“盘口轨迹直接修平局质量”这一候选，不等于所有历史数据路线失败。

## 完整R45B前向轨仍保持

- branch: `research/r45b-pit-role-availability-zero-label`
- HEAD: `c5625410463436c2d2bfa10b67634d92644dfdc6`
- fully eligible: 2 / 300
- remaining: 298
- completed: #1 Alaves–Getafe；#2 Sevilla–Rayo
- sample_frozen: false
- independent_oos_authorized: false
- target labels / training / scoring / tuning / Provider / paid Provider / formal_weight: 全部0

## 禁止重复

- 不得在同一300场上根据结果继续换特征、调阈值、调lambda、candidate search或反复重训。
- 不得把Top1 Draw 0→7当成成功，因为proper-score、AUC、accuracy均明显恶化且7场只命中1场。
- 不得把本历史300场称为independent OOS或完整R45B验证。
- 不重做完整R45B前向fixture #1/#2。
- 不调用付费Provider，不修改正式模型/formal_weight/config/CURRENT。

## 下一步选择

- 若用户继续“用历史数据研究平局”：必须换**新的信息源/特征家族**并先固定新预注册，不得回炉调本300场。
- 若用户说“继续完整R45B/继续前向”：从fixture #3恢复2/300前向积累。
- 若用户问这300场结果：直接报告“仅盘口轨迹draw-mass方案明确失败”，并给上述proper-score/AUC证据。

## 权威优先级

用户当前明确指令 > 唯一正式CURRENT（正式规则事项） > ACTIVE_CHECKPOINT实时断点 > LAST_HANDOFF > PROJECT_CURRENT + Airtable当前状态/绑定维护日志 > 历史聊天/旧文件/记忆。
