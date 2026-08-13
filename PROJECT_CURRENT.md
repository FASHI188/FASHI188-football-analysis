# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 9
- updated_at_utc: 2026-08-12T17:16:10Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_GITHUB_AIRTABLE_FILE_LIBRARY_RESEARCH
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态

- status: WAITING
- current_phase: R44L4_WSL_EXTERNAL_DOMAIN_STOP_UNDERPOWERED_FIND_NEXT_UNTOUCHED_DOMAIN
- current_objective: R44L4 已在独立外部赛事域 FA Women's Super League 按冻结方法测试“当前XI严格prior攻击产出 + 双方攻击平衡”。零标签覆盖457/457通过；开标签后的目标二分类有效样本仅172场/65平局，低于预注册250场最低门，因此在任何模型拟合前按规则停止。该结果不是科学FAIL，效果仍未知；WSL样本已消费，不得在同一确认中追加赛季、放宽门槛或重拟合。
- exact_head: 08b88d51c976da46f78e7125744a702e3c94be55
- branch: research/r44l4-wsl-attack-balance
- pull_request: NONE_FOR_R44L4
- pull_request_state: NOT_APPLICABLE
- background_governed_pr: #176
- background_branch: research/v520-public-web-forward-pit-r44a
- background_pr_state: VERIFIED_OPEN_DRAFT_UNMERGED
- formal_weight: 0
- latest_completed_research: r44l4_wsl_attack_balance
- latest_research_branch: research/r44l4-wsl-attack-balance
- latest_research_executed_head: 08b88d51c976da46f78e7125744a702e3c94be55
- latest_research_verdict: STOP_UNDERPOWERED_AFTER_LABEL_OPEN

`exact_head` 表示最新已执行的 R44L4 研究 HEAD。PR #176 是独立冻结背景工作对象，仍为 Draft/Open/未合并；R44L4 的完成不构成 #176 的执行、Ready 或 merge 授权。

## 当前证据

- startup_rule: 唯一正式 CURRENT 为 `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`；CURRENT 数量=1；V5.2.0 要求新 PIT 信息先零标签覆盖，再做时间顺序/forward OOS 增量验证，覆盖不足时在拟合前 STOP。
- r44l2_prior_result: 300场、3×100时间顺序OOS已完成；“真实阵型+逐场细位置+XI连续性/结构变化”相对B0明显恶化，科学裁决 `RETROSPECTIVE_SIGNAL_FAIL`。该同源特征家族不得继续换模型/正则/阈值烧新样本。
- r44l4_zero_label_gate: run `31620958202`，Artifact `9151202292`，SHA-256 `f988259b63756aef81960e3846ae42780f9621fdcb01f1df7af64aca675c4cc4`；WSL四季零标签覆盖457/457，lineup 457/457，双方XI=11为457/457；40场事件schema抽查均具xG与shot-assist→shot-xG连接；label_fields_accessed=0，model_fits=0。
- r44l4_model_gate_run: run `31621420360`，job `94196931885`，Artifact `9151405073`，SHA-256 `738281594ae6fb934a7356cfa52df9600a5121eec85aacf0b6fdadc74c13e0f4`。
- r44l4_label_open_counts: 2019/20=44场/12平；2020/21=58场/29平；2023/24=70场/24平；合计172场/65平。
- r44l4_preregistered_sample_gate: 要求目标二分类总样本>=250、各时间折>=50；实际 `target_binary_ge_250=FAIL`，2019/20 fold>=50=FAIL；因此终态 `STOP_UNDERPOWERED_AFTER_LABEL_OPEN`。
- r44l4_model_fit: 0。未产生 ΔLogLoss、AUC、Brier、RPS 或Top1效果结论；不得写成信号有效或无效。
- r44l4_consumption: WSL四季样本已经开标签并消费；不得通过追加赛季、降低250门、改变折定义或在172场上拟合来冒充同一独立确认。
- historical_lineup_pit: `UNVERIFIED`。历史首发/阵型逐场 available_at 仍没有可证明的赛前时间戳，因此任何回顾性正结果也不能直接获得正式权重。
- unknown_facts: 正式 CURRENT 原始 DOCX 字节 SHA-256 当前仍未取得可计算原始字节，不得编造。
- evidence_observed_at: 2026-08-13T01:16:10+08:00

## 允许事项

- 修复 state_version 9 的 GitHub/Airtable 接续同步，不改变研究结论。
- 只读复盘 R44L2 与 R44L4。
- 继续平局主线时，优先寻找信息本质不同且可验证赛前可用的新轴；任何新研究必须重新预注册。
- 若继续外部赛事域确认，必须在零标签阶段先证明目标有效样本规模足够，预期总二分类样本>=250且可形成至少3个时间顺序OOS块；不足即STOP，不读标签。
- 优先新信息：可验证时间戳的职责/战术任务/定位球职责，或与既有阵型位置聚合不同的机会数量×机会质量结构。
- PR #176 只允许只读核验，除非用户另行明确授权其执行动作。
- 任何新的重要状态变化必须递增 state_version 并完成 GitHub+Airtable 闭环。

## 禁止事项

- 禁止把 R44L4 写成科学FAIL或PASS；正确状态是覆盖通过后因172<250而 `STOP_UNDERPOWERED_AFTER_LABEL_OPEN`，模型拟合=0。
- 禁止在 R44L4 WSL 内追加赛季、降低门槛、改变折定义或拟合172场来“救”结果。
- 禁止继续“阵型标签+位置计数+XI连续性→机会质量”的等价变体烧新保护样本。
- 禁止给 R44L2、R44L4 或其他未晋级研究非零 formal_weight。
- 禁止访问 Provider、API-Football、付费接口或创建/使用 Secret，除非用户另行明确授权且正式规则允许。
- 禁止修改正式模型、正式数据、config、正式预测逻辑或正式 CURRENT 正文，除非用户另行明确授权且正式规则允许。
- 禁止执行、转Ready或合并 PR #176 或其他PR，除非后续取得对应独立明确授权。
- 禁止删除或改写 R44L2/R44L4 历史证据。

## 唯一下一步

- 在真正未看且样本更大的外部赛事域做**零标签** identity / lineup / XI / event-schema / 规模筛选；只有预先证明可支持预注册最低样本量后，才冻结方法并开标签。
- 不重用或放宽 R44L4 WSL。
- 若找不到满足规模与PIT/事件字段要求的未看域，停止该首发/机会质量路线，切换到信息本质不同的平局轴。

## 停止条件

- `PROJECT_CURRENT.md` 与 Airtable《当前状态》的 `project_id`、`state_version`、当前阶段、当前目标、HEAD/背景PR、允许事项、禁止事项、唯一下一步或绑定日志ID任一不一致：`BLOCKED_STATE_MISMATCH`。
- Airtable无法读取：`BLOCKED_AIRTABLE_UNAVAILABLE`，只允许回答、解释和只读盘点。
- Airtable中 `项目名称=足球项目` 的当前唯一记录异常或存在多条激活状态：`BLOCKED_STATE_MISMATCH`。
- 涉及新的模型研究/训练/评分/晋级/CURRENT修改时，唯一正式CURRENT数量必须严格=1且可完整读取，否则 `BLOCKED_CURRENT_RULE_COUNT_MISMATCH` / `BLOCKED_CURRENT_RULE_UNAVAILABLE`。
- 新研究零标签覆盖低于预注册最低样本或不能形成要求的时间顺序OOS结构时，必须在拟合前 STOP。
- 发现研究样本与既有已消费样本重叠、身份不清、PIT资格无法证明或结果标签提前进入选择流程时立即停止。

## 正式规则状态

- current_rule_required: true
- current_rule_count: 1
- current_rule_identity: 足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx
- current_rule_sha256: UNKNOWN_RAW_BYTES_NOT_AVAILABLE
- current_rule_status: VERIFIED_FULL_READ_AND_APPLIED

V5.2.0 仍是唯一正式规则；本次仅修复接续状态，不改变正式权重、正式模型、正式数据、config或CURRENT。

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: rec0SbXebFVqwfXJZ
- airtable_state_version: 9
- airtable_sync_status: PENDING_GITHUB_STATE9_SYNC
