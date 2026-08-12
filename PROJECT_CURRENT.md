# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 6
- updated_at_utc: 2026-08-12T09:37:07Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_GITHUB_AIRTABLE_FILE_LIBRARY_RESEARCH
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态

- status: WAITING
- current_phase: LINEUP_OPPORTUNITY_300_R1_COMPLETED_WAITING_USER_DIRECTION
- current_objective: 300场“首发结构×预期机会质量”R1已按预注册完成真实时间顺序OOS；零标签Gate通过，但科学效果裁决为RETROSPECTIVE_SIGNAL_FAIL。R1保持formal_weight=0，不得晋级。冻结背景工作对象PR #176保持Open/Draft/未合并且未执行；等待用户决定是否建立信息质量更高的R2或转向其他研究轴。
- exact_head: 06a0825308573420e4c0096c157d52adf7b8d7d2
- branch: research/v520-public-web-forward-pit-r44a
- pull_request: #176
- pull_request_state: VERIFIED_OPEN_DRAFT_UNMERGED
- formal_weight: 0
- latest_completed_research: lineup_opportunity_300_r1
- latest_research_branch: research/lineup-opportunity-300-r1
- latest_research_executed_head: bd688e673ad07b9f65fc8ef985ad29d2c2d50814
- latest_research_evidence_head: 69c3a99a2d342a8b774b8aac03fd04f714682973
- latest_research_verdict: RETROSPECTIVE_SIGNAL_FAIL

`exact_head` / `branch` / `pull_request`继续表示冻结的被治理背景工作对象PR #176；本次300场研究是用户明确启动的独立、formal_weight=0回顾性研究轨，不改变#176身份，也不代表#176获得执行授权。

## 当前证据

- verified_facts: 本对话启动时已重新检索并完整读取唯一正式CURRENT V5.2.0，CURRENT数量=1，启动门通过；GitHub实时核验PR #176仍Open/Draft/未合并且HEAD=06a0825308573420e4c0096c157d52adf7b8d7d2；用户随后明确要求启动300场首发结构+预期机会量研究。
- latest_research_run: GitHub Actions run 31583239963，executed HEAD=bd688e673ad07b9f65fc8ef985ad29d2c2d50814，状态completed/success。zero-label-gate job=94070993260 success；model-oos job=94071191738 success。
- latest_research_gate: 300场、600 team-match；10个fixture+element重复key按预注册规则确定性去重；冲突重复=0；必需字段缺失=0；600/600恰11名首发；600/600恰1名GK；位置完整600/600；anomaly_count=0；Gate阶段未加载xG/xA标签。
- latest_research_primary: B0 pooled xG NLL=0.2242526999；L1=0.2418099054；ΔNLL(L1-B0)=+0.0175572055。3个连续100场fold的ΔNLL分别+0.0346885301、+0.0018265556、+0.0161565308，0/3 fold改善。10,000次match-cluster bootstrap、seed=20260812的90%区间=[+0.0032075272,+0.0320401040]。
- latest_research_secondary: xG RMSE 0.74978258→0.75331356，MAE 0.58142698→0.59260565；xG校准slope 1.03950888→0.69589151。xA pooled ΔNLL=+0.0380764304，RMSE 0.47246585→0.48178949，MAE 0.35847061→0.37079523；xA MAE超过预注册2%恶化门。
- robustness: 删除任意单队后xG pooled ΔNLL仍全部>0，因此R1失败不是由单一球队异常造成；单一球队正向改善贡献最高占比约26.78%，通过<50%集中度门。
- research_verdict: RETROSPECTIVE_SIGNAL_FAIL。准确含义是“当前预注册的FPL粗位置壳+首发连续性+严格历史xG/xA/xGI聚合没有形成可泛化team xG增量”；不得扩大为“所有首发信息无价值”。
- research_artifacts: Gate Artifact 9136027911，SHA-256=b92d315ca3868e11a0b6539779b5605d46b7b90695f414b34474d54e6ae6eece；Model Artifact 9136052161，SHA-256=73a03ae355e88ea4dc6e66e79495723ae46d4d548416aefa0baa86bae3f57270。
- research_source_identity: vaastav/Fantasy-Premier-League source commit 8c97b2adb123863c3dd581e730f1360e89815ac2；fixtures.csv SHA-256=2d7e3950d346df14ca486cb09e9b9ba406d37d943775244eed06cdc021ffb3a9；merged_gw.csv SHA-256=0d09f1f1cb1b5520ec8e2f25238aa652efe2a263d8ca7cb2b6538b27bf86727d。
- historical_lineup_pit: UNVERIFIED。历史首发逐场available_at未取得可证明的赛前时间戳，因此该研究即使通过也不能自动获得SCIENTIFIC_COMPONENT_PASS；本次实际已FAIL。
- reported_not_verified: 未在本状态版本逐项实时复核的其他历史PR、run、Artifact和旧聊天指标，继续保持REPORTED_NOT_VERIFIED或UNKNOWN。
- inferred_facts: FPL的DEF/MID/FWD粗位置壳与29维聚合可能造成信息表达不足/泛化损失，是R1失败的合理候选解释之一，但尚未因果验证；不得写成已证实根因。
- unknown_facts: 正式CURRENT原始DOCX字节SHA-256当前仍未取得可计算字节，不得编造。
- evidence_observed_at: 2026-08-12T09:37:07Z

## 允许事项

- 只读复盘 `lineup_opportunity_300_r1` 的预注册、执行冻结、run、Artifact、预测和审计结果。
- 若用户要求继续首发研究，允许建立新的R2，但必须先写新的预注册并保持R1原结果不可改写；优先研究真实赛前角色/站位/球员职责或其他信息质量更高的首发结构表示，而不是在R1内赛后调参。
- 允许继续独立的严格PIT信息轴研究，但不得把本R1结果混入正式模型。
- 回答用户关于项目现状、下一步和研究结果的问题。
- 任何新的重要状态变化必须再次递增 `state_version` 并执行GitHub+Airtable闭环同步。

## 禁止事项

- 禁止把Run 31583239963的workflow success解释为科学PASS；其科学裁决固定为RETROSPECTIVE_SIGNAL_FAIL。
- 禁止给lineup_opportunity_300_r1非零正式权重，禁止声称已晋级正式模型。
- 禁止在R1内赛后更换alpha、挑fold、删球队、增加交互或新特征以“救”结果；任何新假设必须成为独立R2。
- 禁止把R1 FAIL扩大解释为“所有首发信息都没有价值”或“真实战术阵型没有价值”。
- 禁止读取新的盲测/holdout标签，除非用户另行明确授权且正式规则允许。
- 禁止访问Provider、API-Football、付费接口或使用/创建Secret，除非用户另行明确授权且正式规则允许。
- 禁止修改正式模型、正式数据、config、正式预测逻辑或正式CURRENT正文，除非用户另行明确授权且正式规则允许。
- 禁止执行、转Ready或合并PR #176或其他PR，除非后续取得对应独立明确授权。
- 禁止删除历史证据或改写已冻结R1结果。

## 唯一下一步

- 等待用户下一步指令。若继续“首发结构→机会质量”方向，不重复R1；建立R2并把主要变量从FPL粗位置壳升级到更细、可赛前获得且可验证时间戳的真实角色/站位/职责信息，再做新的严格时间顺序OOS。若拿不到更细信息，则停止在同类首发聚合上继续消耗样本。

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
- current_rule_status: VERIFIED_FULL_READ_AND_APPLIED_AT_LINEUP_OPPORTUNITY_300_R1_START

本次研究已经按V5.2.0启动协议完成并关闭；当前处于WAITING。新的正式研究或预测任务仍需按项目协议检查规则门，本文件不能替代正式CURRENT正文。

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: recpeMvQLYqjsNuOe
- airtable_state_version: 6
- airtable_sync_status: STATE_SYNC_VERIFIED
