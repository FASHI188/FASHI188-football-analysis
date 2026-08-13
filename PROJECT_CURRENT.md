# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 10
- updated_at_utc: 2026-08-13T02:07:17Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_GITHUB_AIRTABLE_FILE_LIBRARY_RESEARCH
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态

- status: WAITING
- current_phase: R44L7C_SETPIECE_ROLE_EFFECT_PREREG_FROZEN_AWAITING_EXPLICIT_LABEL_AUTHORIZATION
- current_objective: R44L7/R44L7B 已证明“近期角球/任意球实际执行职责是否被当前首发保留”这一新信息轴可以严格 prior-only、零目标标签地稳定重建并全量覆盖；R44L7C 已冻结未来一次“平局 vs 一球胜负”增量效果验证合同，但尚未获得新的目标标签访问授权，因此没有读取目标比分、没有模型拟合、没有候选概率或科学效果结论。
- exact_head: 72f36df4b0318e419eebffa90a566b8182462740
- branch: research/r44l7c-setpiece-role-effect-prereg
- pull_request: #184
- pull_request_state: VERIFIED_OPEN_DRAFT_UNMERGED
- formal_weight: 0
- latest_completed_research: r44l7b_setpiece_full_zero_label
- latest_research_executed_head: 5dd07c66634779609b4691520bc6904a56cd155a
- latest_research_verdict: PASS_R44L7B_FULL_PRIOR_ONLY_ZERO_LABEL_COVERAGE
- latest_preregistration: r44l7c_setpiece_role_effect
- latest_preregistration_status: PRE_REGISTERED_NOT_AUTHORIZED_NOT_RUN
- latest_preregistration_contract_sha256: 74fc844324569dff8d4d6c836ded9872498fa378fd57f88f19b188b64a9979c2

## 当前证据

- startup_rule: 唯一正式 CURRENT 为 `足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`；CURRENT 数量=1；本对话已完整读取并按V5.2.0执行。
- recovered_r44l5: branch=`research/r44l5-ligue1-attack-balance`，HEAD=`47aaf4d0b1e61f2974671e22f83e20c977c1d0ae`；run=`31622015101`；Ligue1身份435场，赛季结构377/26/32，首发/事件结构可用但规模门失败；terminal=`STOP_DATA_COVERAGE_R44L5`；目标标签=0、模型拟合=0。
- recovered_r44l6: branch=`research/r44l6-statsbomb-domain-census`，HEAD=`6893cecc9462e680c224a0a2ac718c92048ac4b9`；run=`31622287667`；80个competition-season身份条目、identity failures=0；该公开源没有足够多个完整连续赛季用于继续同一“攻击质量外部域”路线。
- redundant_r44l5_stage_a: PR #181 Draft/Open/未合并；HEAD=`5aa3ff6c1ccaa706bdfd9e53d1a6dd5928314256`；run=`31658603296`；更严格全域规模密度门得到0候选，terminal=`STOP_R44L5_NO_EXTERNAL_DOMAIN_MEETS_SCALE_DENSITY_GATE`；仅作为R44L6结论的冗余零标签确认，不构成新科学效果。
- r44l7_zero_label: PR #182 Draft/Open/未合并；HEAD=`a4d70cdd67774b1451f3afb0d83c18499f8257e1`；run=`31659083509`，job=`94319889668`；24/24目标场严格11v11、48/48 team-target完整prior5、48/48可观察>=3次Corner/Free Kick执行；XI role retention中位数=`0.7654863901736295`；Top-1执行人仍在当前XI=`0.7916666666666666`；terminal=`PASS_R44L7_SETPIECE_ROLE_ZERO_LABEL_FEASIBILITY`；Artifact=`9165539857`，SHA-256=`8375a705757561fe39ba503b119f1aeb5b3b6300a358049f87f0907e9e2e53d5`；目标结果=0、目标event=0、模型拟合=0。
- r44l7b_full_zero_label: PR #183 Draft/Open/未合并；final HEAD=`5dd07c66634779609b4691520bc6904a56cd155a`；run=`31659501770`，job=`94321145557`；identity_total=1137，event files=1137/1137，target matches=837，target lineups=837/837，strict11v11=836/837，team-targets=1674，complete prior5=1674/1674，setpiece observable=1674/1674，XI role retention中位=`0.7546312178387651`，Top-1执行人仍在XI=`0.8225806451612904`，same-match event倒灌=0；全部预注册覆盖门PASS；terminal=`PASS_R44L7B_FULL_PRIOR_ONLY_ZERO_LABEL_COVERAGE`；Artifact=`9165673664`，SHA-256=`f5c5bcb2250129efd19cf629149199d9c9c1a6f9faec792e5e7e046c3c97d4a6`。
- r44l7b_engineering_false_starts: run `31659337793` 与 `31659445517` 均在静态合同字符串误报检查阶段停止，核心全量覆盖步骤skipped，未读取目标结果、未消耗效果样本；最终只修复静态检查，不改科学预注册。
- r44l7c_prereg: PR #184 Draft/Open/未合并；HEAD=`72f36df4b0318e419eebffa90a566b8182462740`；run=`31659813478`，job=`94322075987` completed/success；状态=`PRE_REGISTERED_NOT_AUTHORIZED_NOT_RUN`；contract SHA-256=`74fc844324569dff8d4d6c836ded9872498fa378fd57f88f19b188b64a9979c2`；CI确认目标标签访问=0、模型拟合=0、候选概率=0，并确认不存在R44L7C标签执行脚本或标签执行workflow。
- r44l7c_frozen_science: baseline=双方prior10 GF/GA/PPM/GD/draw rate + 主客身份 + competition fixed effects；candidate只新增双方各5项prior5定位球职责字段；标准化L2 Logistic，C=1.0；837场post-warmup容量切3个连续chronological OOS folds；打开标签后必须先通过总binary>=450、draw>=120、每域binary>=120、每域draw>=30、每fold binary>=120的样本门，否则model_fits=0。
- r44l7c_science_gate: PASS必须同时满足 pooled ΔLogLoss<0、其90% match-cluster bootstrap上界<0、pooled ΔBrier<0、至少2/3时间折ΔLogLoss<0、至少2/3赛事域ΔLogLoss<0；即便PASS也只可写 `RETROSPECTIVE_SETPIECE_ROLE_INCREMENT_PASS_REQUIRES_INDEPENDENT_CONFIRMATION`，formal_weight仍为0。
- viewed_exclusion: La Liga 2015/16 `(competition_id=11, season_id=27)` 因本对话schema查看时比赛容器结果字段已展示，永久状态=`VIEWED_EXCLUDED_RESULT_FIELDS_DISPLAYED_DURING_SCHEMA_INSPECTION`，不得进入R44L7C效果样本。
- unknown_facts: 正式CURRENT原始DOCX字节SHA-256当前仍未取得可计算原始字节，不得编造。
- evidence_observed_at: 2026-08-13T10:07:17+08:00

## 允许事项

- 只读核验R44L5/R44L6/R44L7/R44L7B/R44L7C证据与合同。
- 回答当前项目状态、研究结论、风险与下一步。
- 用户若后续明确单独授权“按冻结R44L7C预注册读取目标标签并执行一次效果验证”，可在不改变任何冻结科学合同的前提下创建一次性标签执行路径；执行时必须先做样本量门，未通过则model_fits=0并停止。
- 普通接续治理与证据修复可以继续，但不得改变冻结研究结论。

## 禁止事项

- 未获新的明确标签授权前，禁止读取R44L7C目标比分/标签、创建或运行标签执行器、拟合模型、计算候选概率或效果指标。
- 禁止修改R44L7C的prior5/prior10窗口、10项候选职责字段、C=1.0、三折OOS、样本量门、bootstrap设置或科学PASS门。
- 禁止加入La Liga 2015/16，禁止在看到结果后删联赛、挑fold、加交互、筛字段、反转变量或重跑失败确认。
- 禁止把R44L7/R44L7B的零标签PASS写成“定位球职责能预测平局”或“平局问题已解决”。
- 禁止给R44L7/R44L7B/R44L7C非零formal_weight。
- 禁止将PR #181/#182/#183/#184转Ready或合并，除非用户另行明确授权。
- 禁止执行/转Ready/合并冻结背景PR #176。
- 禁止访问Provider、API-Football、付费接口或创建/使用Secret，除非用户另行明确授权且CURRENT允许。
- 禁止修改正式模型、正式数据、config、正式预测逻辑或正式CURRENT正文。

## 唯一下一步

- 等待用户新的明确授权：`按冻结R44L7C预注册读取目标标签并执行一次效果验证`。
- 未获得该独立授权前，只读，不创建/运行R44L7C标签执行路径。

## 停止条件

- `PROJECT_CURRENT.md` 与 Airtable《当前状态》的 project_id、state_version、当前阶段、当前目标、当前HEAD/分支/PR、允许事项、禁止事项、唯一下一步、绑定日志ID或SHA-256任一不一致：`BLOCKED_STATE_MISMATCH`。
- Airtable无法读取：`BLOCKED_AIRTABLE_UNAVAILABLE`，只允许回答、解释和只读盘点。
- Airtable当前唯一足球项目记录异常或出现多条激活记录：`BLOCKED_STATE_MISMATCH`。
- 涉及新的模型研究/训练/评分/晋级/CURRENT修改时，唯一正式CURRENT数量必须严格=1且可完整读取，否则 `BLOCKED_CURRENT_RULE_COUNT_MISMATCH` / `BLOCKED_CURRENT_RULE_UNAVAILABLE`。
- R44L7C未来若获得标签授权，打开标签后任何样本量硬门失败：`STOP_R44L7C_UNDERPOWERED_AFTER_LABEL_OPEN`，model_fits=0。
- 发现La Liga 2015/16进入效果样本、同场event倒灌、训练时间不严格早于OOS fold、冻结参数被修改或标签提前进入选择流程时立即停止。

## 正式规则状态

- current_rule_required: true
- current_rule_count: 1
- current_rule_identity: 足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx
- current_rule_sha256: UNKNOWN_RAW_BYTES_NOT_AVAILABLE
- current_rule_status: VERIFIED_FULL_READ_AND_APPLIED
- formal_weight_change: 0

V5.2.0仍是唯一正式规则。R44L7/R44L7B只证明定位球职责数据轴可稳定prior-only重建；R44L7C只完成预注册，尚无平局增量效果结论。

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: recLWuFtzbzShbeYD
- airtable_state_version: 10
- airtable_sync_status: STATE_SYNC_VERIFIED
