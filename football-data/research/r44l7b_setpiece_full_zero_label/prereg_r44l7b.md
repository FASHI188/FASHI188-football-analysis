# R44L7B 定位球职责全量 prior-only 零标签覆盖：冻结预注册

## 1. 上游证据

R44L7 Stage-A 已在固定24场/48个team-target上通过全部零标签可重建门：目标首发24/24恰11v11、prior5完整48/48、定位球可观察48/48、XI role retention中位数0.7654863902、Top-1执行人仍在目标XI比例0.7916666667。该结果仅证明可重建，不证明平局效果。

## 2. 本阶段问题

在不读取任何目标比分/胜负标签、不拟合模型的前提下，将同一职责算法扩展到英超/意甲/法甲2015/16三个完整赛事域的**全部可用时间顺序目标场**，验证：

1. 当前XI能否大规模稳定读取；
2. 每场特征是否严格只由该场之前的事件历史形成；
3. 同一场事件只能在该场特征行封存之后进入历史，绝不能倒灌到自身特征；
4. 总目标容量是否足以进入下一阶段独立预注册。

## 3. 固定数据身份

source=`hudl/open-data@b0bc9f22dd77c206ddedc1d742893b3bbe64baec`

- Premier League: competition_id=2, season_id=27
- Serie A: competition_id=12, season_id=27
- Ligue 1: competition_id=7, season_id=27

La Liga 2015/16固定排除，不得进入未来效果样本。

## 4. 时间顺序算法

每赛事域按 `(match_date, kick_off, match_id)` 排序。

- 前100场仅作职责历史warm-up，不产生正式候选特征行；
- 第101场起每场均产生目标特征行；
- 对目标场双方，当前XI仅从该场lineup文件中 `from=00:00 && start_reason=Starting XI` 取得；
- prior5为该队此前最近5场；
- 只统计prior5中 `Pass` 且 `pass.type.name in {Corner, Free Kick}` 的执行人；
- 先生成并封存目标场职责特征，再允许读取该场event并把其中定位球执行记录加入未来历史；
- 当前场event不得参与当前场自身特征。

## 5. 输出字段（零标签）

每个team-target固定输出：
- domain / match_id / match_date / team_id / is_home
- current_xi_n
- prior_match_count
- prior5_setpiece_passes
- prior5_unique_takers
- prior5_top1_share
- prior5_top1_taker_in_xi
- prior5_xi_role_retention

比赛级文件只拼接主客两行，不生成任何标签或概率。

## 6. 全量覆盖门

固定预期身份：380 + 380 + 377 = 1137场；warm-up后目标容量：280 + 280 + 277 = 837场、1674个team-target。

必须全部满足：
1. 三赛事域identity分别>=370且总identity>=1130；
2. target match rows>=830；
3. target lineup文件成功率>=0.98；
4. 双方恰11名首发的target match比例>=0.97；
5. complete prior5 team-target比例>=0.98；
6. prior5定位球执行>=3次的team-target比例>=0.90；
7. XI role retention中位数>=0.60；
8. Top-1执行人进入当前XI比例>=0.70；
9. same_match_event_used_before_feature=0；
10. result/label keys accessed=0；model_fits=0；candidate_probabilities=0。

全部通过：`PASS_R44L7B_FULL_PRIOR_ONLY_ZERO_LABEL_COVERAGE`。
任一失败：`STOP_R44L7B_FULL_PRIOR_ONLY_COVERAGE`。

## 7. 下一阶段

PASS后仍不得开标签。只能先创建独立R44L7C效果预注册，冻结：
- 明确baseline；
- 定位球职责候选字段；
- chronological/domain OOS；
- 标签打开后的最低有效binary样本门；
- LogLoss/Brier/AUC与跨域稳定门；
- 不得在结果出现后换窗口、删联赛或挑字段。

读取任何新的效果标签仍需独立明确授权。
