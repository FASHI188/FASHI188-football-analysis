# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 7
- updated_at_utc: 2026-08-12T13:21:49Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_GITHUB_AIRTABLE_FILE_LIBRARY_RESEARCH
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态

- status: WAITING
- current_phase: R44L2_LINEUP_ROLE_OPPORTUNITY_300_COMPLETED_WAITING_USER_DIRECTION
- current_objective: R44L2“真实阵型/逐场细位置×预期机会质量”已按冻结方案完成300场、3×100时间顺序OOS。300场零标签门和380场训练支撑门均通过，但科学效果裁决为RETROSPECTIVE_SIGNAL_FAIL，且效果比R1更差。R44L2保持formal_weight=0，不得晋级。冻结背景工作对象PR #176继续Open/Draft/未合并且未执行；等待用户决定回到平局主线或建立信息类型真正不同的新研究轴。
- exact_head: 06a0825308573420e4c0096c157d52adf7b8d7d2
- branch: research/v520-public-web-forward-pit-r44a
- pull_request: #176
- pull_request_state: VERIFIED_OPEN_DRAFT_UNMERGED
- formal_weight: 0
- latest_completed_research: r44l2_lineup_role_opportunity_300
- latest_research_branch: research/r44l2-lineup-role-opportunity-300
- latest_research_executed_head: cb15abe88ab6a359143f297d36e1066714a0d37d
- latest_research_evidence_head: 7c92303d429e58e7090af219983638a6855ace73
- latest_research_verdict: RETROSPECTIVE_SIGNAL_FAIL

`exact_head` / `branch` / `pull_request`继续表示冻结的被治理背景工作对象PR #176；R44L2是用户明确启动的独立、formal_weight=0回顾性研究轨，不改变#176身份，也不代表#176获得执行授权。

## 当前证据

- startup_rule: 本对话首次足球研究前已检索并完整读取唯一正式CURRENT V5.2.0；CURRENT数量=1，启动门通过。
- r44l2_zero_label_gate: GitHub Actions run `31594761068`，executed HEAD=`d03f58ecfe6d675f6c0147b44466500d447f7536`，completed/success；300/300比赛、600/600 team-match，全部恰11名首发，逐场position非空，formation非空，身份无缺失/歧义，冲突重复=0；terminal=`PASS_R44L2_ZERO_LABEL_GATE`。Artifact ID=`9140567749`，SHA-256=`c3b1082eff063589b7ba455cf101f7cd1a9057fcd1a0d4a742159ec9eeaf1c2c`。
- r44l2_training_support_gate: GitHub Actions run `31595059732`，executed HEAD=`952637f26de5317bb7537faeb3986c203048f2e3`，completed/success；380/380比赛、760/760 team-match均可用，源哈希与300场门一致；terminal=`PASS_R44L2_TRAINING_SUPPORT_380`。Artifact ID=`9140682873`，SHA-256=`2b8a8a768b2d92f692105135a40f9b2c10b4c70a71b38126317bd4882f2ce176`。
- r44l2_model_run: GitHub Actions run `31595573285`，job=`94110122955`，executed HEAD=`cb15abe88ab6a359143f297d36e1066714a0d37d`，completed/success；冻结文件SHA-256=`3294e728a87d9e032ffc6b473555539b43e7475801e2077b2afd4cd628d0bd2a`；Model Artifact ID=`9140896160`，SHA-256=`fc3541f099c1391da0761285bda87070291427ea81e570008a07c35572a888e4`。
- r44l2_algorithm: B0固定5特征；候选共26特征，新增真实逐场位置计数、阵型线结构、prev_xi_overlap、recent5_top11_overlap、formation_changed、role_l1_change；standardized Ridge alpha=10；最后300场为正式样本，80场warm-up；3个连续100场OOS fold；10,000次match-cluster bootstrap，seed=20260812。
- r44l2_primary: B0 pooled xG NLL=`0.22425269987027927`；R44L2=`0.2673888051708597`；ΔNLL=`+0.04313610530058047`。3个fold ΔNLL分别=`+0.07817268656787325`、`+0.04538092733788943`、`+0.005854701995978729`，0/3改善。bootstrap 90%区间=`[+0.023258727719036025,+0.06369725185650359]`，全区间>0。
- r44l2_point_metrics: xG RMSE `0.7497825818942667 -> 0.7780617077163459`；xG MAE `0.5814269818228752 -> 0.6101138742280312`；xG calibration slope `1.039508875268133 -> 0.6219209179776092`，intercept `0.08637541580581075 -> 0.632822556175259`。
- r44l2_secondary: xA pooled ΔNLL=`+0.014309872020214533`；RMSE `0.4724658526916336 -> 0.47877059519988896`；MAE `0.35847061423757454 -> 0.3635290458775227`。仅fold3 xA NLL改善，pooled仍更差。
- r44l2_robustness: 删除任意单队后xG pooled ΔNLL仍全部>0；正向改善贡献最高球队Everton占比约63.90%，未通过<50%集中度门。
- r44l2_gates: pooled_delta_negative FAIL；bootstrap_upper_negative FAIL；fold_improvement_at_least_2_of_3 FAIL；xg_rmse_no_material_worse FAIL；xg_mae_no_material_worse FAIL；xa_rmse_no_material_worse PASS；xa_mae_no_material_worse PASS；leave_one_team_out_all_negative FAIL；single_team_improvement_share_below_50pct FAIL。
- research_verdict: `RETROSPECTIVE_SIGNAL_FAIL`。准确含义是：在本次冻结表示下，Transfermarkt真实阵型+逐场细位置计数+首发连续性/结构变化没有形成B0之上的可泛化team xG增量，反而明显恶化NLL、RMSE、MAE与校准。不得扩大为“所有首发/战术信息无价值”。
- comparison_with_r1: R1 pooled xG ΔNLL=`+0.0175572055`；R44L2=`+0.0431361053`。因此本次更细阵型/位置表示没有修复R1问题，反而在同一B0/同一正式300场上产生更大的总体损失。
- historical_lineup_pit: `UNVERIFIED`。历史首发/阵型的逐场available_at仍无可证明赛前时间戳，因此即便信号为正也不能自动获得SCIENTIFIC_COMPONENT_PASS；本次实际已FAIL。
- latest_result_audit: `football-data/research/r44l2_lineup_role_opportunity_300/result_audit_r44l2.md`，evidence HEAD=`7c92303d429e58e7090af219983638a6855ace73`。
- reported_not_verified: 未在本状态版本逐项实时复核的其他历史PR、run、Artifact和旧聊天指标继续保持REPORTED_NOT_VERIFIED或UNKNOWN。
- unknown_facts: 正式CURRENT原始DOCX字节SHA-256当前仍未取得可计算字节，不得编造。
- evidence_observed_at: 2026-08-12T13:21:49Z

## 允许事项

- 只读复盘R1与R44L2，比较为何两种首发结构表示都没有形成稳定增量。
- 回到平局主线，优先使用与已失败首发聚合不同的信息轴或结构假设，并为任何新实验独立预注册。
- 若继续首发方向，只允许研究信息类型明显不同且赛前可观测的变量，例如可验证时间戳的角色职责、定位球职责、战术任务或真实赛前阵容变化；不得把R44L2事后调参包装成新证据。
- 回答用户关于项目现状、下一步和研究结果的问题。
- 任何新的重要状态变化必须再次递增 `state_version` 并执行GitHub+Airtable闭环同步。

## 禁止事项

- 禁止把run `31595573285`的workflow success解释为科学PASS；科学裁决固定为`RETROSPECTIVE_SIGNAL_FAIL`。
- 禁止给R44L2非零正式权重，禁止声称已晋级正式模型。
- 禁止在R44L2内赛后更换alpha、挑fold、删球队、挑阵型、删除坏球队、增加交互或新特征以“救”结果；任何新假设必须是独立研究。
- 禁止再次用等价的“阵型标签+位置计数+XI连续性”特征烧同一类300场样本，除非有新的独立可证伪假设与预注册。
- 禁止把R44L2 FAIL扩大解释为“所有首发信息都没有价值”或“真实战术信息没有价值”。
- 禁止访问Provider、API-Football、付费接口或使用/创建Secret，除非用户另行明确授权且正式规则允许。
- 禁止修改正式模型、正式数据、config、正式预测逻辑或正式CURRENT正文，除非用户另行明确授权且正式规则允许。
- 禁止执行、转Ready或合并PR #176或其他PR，除非后续取得对应独立明确授权。
- 禁止删除R1或R44L2历史证据或改写已冻结结果。

## 唯一下一步

- 等待用户下一步指令。默认研究建议是停止继续优化当前“首发结构聚合→机会质量”家族，回到平局主线，从尚未被R1/R44L2否定的独立信息轴寻找增量；若仍研究首发，必须换成信息内容本质不同、且可验证为赛前可获得的职责/任务/定位球等变量，而不是继续细化同一位置计数表示。

## 停止条件

- `PROJECT_CURRENT.md` 与 Airtable《当前状态》的 `project_id`、`state_version`、`updated_at_utc`、状态、当前阶段、当前目标、`exact_head`、分支、PR、PR状态、允许事项、禁止事项、唯一下一步、停止条件、绑定日志ID或SHA-256任一不一致：`BLOCKED_STATE_MISMATCH`。
- Airtable无法读取：`BLOCKED_AIRTABLE_UNAVAILABLE`，只允许回答、解释和只读盘点。
- Airtable中 `项目名=足球项目` 且 `是否激活=true` 的记录数量不等于1：`BLOCKED_STATE_MISMATCH`。
- 涉及新的正式预测/模型研究/训练/评分/晋级/CURRENT修改时，必须重新确认唯一正式CURRENT可完整读取；数量不等于1：`BLOCKED_CURRENT_RULE_COUNT_MISMATCH`；无法完整读取：`BLOCKED_CURRENT_RULE_UNAVAILABLE`。
- 发现当前工作对象身份、研究run/Artifact身份或GitHub/Airtable状态互相冲突时立即停止，不得猜测更新的一方。

## 正式规则状态

- current_rule_required: false
- current_rule_count: 1
- current_rule_identity: 足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx
- current_rule_sha256: UNKNOWN_RAW_BYTES_NOT_AVAILABLE
- current_rule_status: VERIFIED_FULL_READ_AND_APPLIED_AT_R44L2_START

本次R44L2研究已按V5.2.0边界完成并关闭；当前处于WAITING。新的正式研究或预测任务仍需按项目协议检查规则门，本文件不能替代正式CURRENT正文。

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: reczcNQmIZXYX1nDm
- airtable_state_version: 7
- airtable_sync_status: STATE_SYNC_VERIFIED
