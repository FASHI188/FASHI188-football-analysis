# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 11
- updated_at_utc: 2026-08-13T02:28:37Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_USER_AUTHORIZATION_GITHUB_AIRTABLE_FILE_LIBRARY
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态

- status: RUNNING
- current_phase: R44L7C_AUTHORIZED_ONE_SHOT_EXECUTION_PRE_LABEL
- current_objective: 用户已在state10唯一下一步语境下明确回复“开始”，授权严格按冻结R44L7C合同打开目标标签并执行一次效果验证。authorization receipt与runner已写入研究分支；冻结contract未修改；标签执行workflow尚未触发，因此本状态写入时R44L7C执行器仍未打开目标标签。
- exact_head: 96053e767fba0fee245a09cefac6c6d4aeb4bf54
- branch: research/r44l7c-setpiece-role-effect-prereg
- pull_request: #184
- pull_request_state: VERIFIED_OPEN_DRAFT_UNMERGED
- formal_weight: 0
- latest_completed_research: r44l7b_setpiece_full_zero_label
- latest_research_verdict: PASS_R44L7B_FULL_PRIOR_ONLY_ZERO_LABEL_COVERAGE
- r44l7c_contract_sha256: 74fc844324569dff8d4d6c836ded9872498fa378fd57f88f19b188b64a9979c2
- r44l7c_authorization: VERIFIED_EXPLICIT_USER_AUTHORIZATION_ONE_SHOT

## 当前证据

- current_rule: 唯一正式CURRENT=`足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`；数量=1；本对话已完整读取并应用。
- upstream_zero_label: R44L7B exact HEAD=`5dd07c66634779609b4691520bc6904a56cd155a`；run=`31659501770`；job=`94321145557`；Artifact=`9165673664`；terminal=`PASS_R44L7B_FULL_PRIOR_ONLY_ZERO_LABEL_COVERAGE`；837场/1674 team-target；same-match event倒灌=0。
- prereg: PR #184 frozen HEAD=`72f36df4b0318e419eebffa90a566b8182462740`；contract SHA-256=`74fc844324569dff8d4d6c836ded9872498fa378fd57f88f19b188b64a9979c2`；固定三域EPL/SerieA/Ligue1 2015/16；La Liga 2015/16永久VIEWED_EXCLUDED。
- authorization: 用户当前消息“开始”按上一轮唯一下一步解析为“按冻结R44L7C预注册读取目标标签并执行一次效果验证”；authorization receipt已独立记录，冻结contract正文未改。
- execution_prep: runner HEAD=`96053e767fba0fee245a09cefac6c6d4aeb4bf54`；固定scikit-learn 1.9.0、L2 Logistic C=1.0、lbfgs、max_iter=2000、3折chronological OOS、10,000 match-cluster bootstrap seed=20260813。
- sample_gate: 打开标签后先计数；总binary>=450、draw>=120、每域binary>=120、每域draw>=30、每fold binary>=120；任一失败固定`STOP_R44L7C_UNDERPOWERED_AFTER_LABEL_OPEN`且predictive_model_fits=0。
- science_gate: pooled ΔLogLoss<0且90% bootstrap上界<0；pooled ΔBrier<0；至少2/3 folds ΔLogLoss<0；至少2/3 domains ΔLogLoss<0。
- formal_changes: model=0；data=0；config=0；CURRENT=0；formal_weight=0。
- evidence_observed_at: 2026-08-13T10:28:37+08:00

## 允许事项

- 仅允许创建并触发一次精确父HEAD绑定的R44L7C执行workflow。
- 执行器可按冻结合同读取三域目标标签；必须先完成样本量门，只有全部通过才允许固定模型拟合。
- 允许只读检查run/job/Artifact/receipt并完成科学裁决与接续闭环。

## 禁止事项

- 禁止修改contract、prior5/prior10、10项定位球职责字段、C=1.0、solver、fold、样本门、bootstrap或科学PASS门。
- 禁止加入La Liga 2015/16、删联赛、挑fold、加交互、筛字段、符号翻转、超参数搜索或事后救援。
- 禁止第二次科学执行；技术前置失败若发生，必须先确认未打开标签才能修复重触发。
- 禁止formal_weight>0、正式模型/数据/config/CURRENT修改、PR Ready或merge。

## 唯一下一步

- 创建只接受父HEAD `96053e767fba0fee245a09cefac6c6d4aeb4bf54` 的一次性workflow并触发R44L7C。
- run完成后只读审计并进入下一state；若样本门失败则0拟合停止，若通过则按冻结科学门裁决一次。

## 停止条件

- contract SHA-256变化、数据源pin变化、三域变化、La Liga进入、父HEAD不符或scikit-learn版本不符：标签前停止。
- 样本量硬门任一失败：`STOP_R44L7C_UNDERPOWERED_AFTER_LABEL_OPEN`，predictive_model_fits=0。
- 同场event进入当前特征、训练时间不严格早于fold起点、训练/测试身份重叠或冻结参数漂移：立即停止。
- PROJECT_CURRENT与Airtable current state_version/HEAD/PR/唯一下一步/绑定日志不一致：`BLOCKED_STATE_MISMATCH`。

## 正式规则状态

- current_rule_required: true
- current_rule_count: 1
- current_rule_identity: 足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx
- current_rule_sha256: UNKNOWN_RAW_BYTES_NOT_AVAILABLE
- current_rule_status: VERIFIED_FULL_READ_AND_APPLIED
- formal_weight_change: 0

## Airtable同步

- airtable_base: 足球项目接续
- current_state_record_id: recs1pQ1rhuwJQAzE
- state_log_record_id: reckTmvXHcR8inwgH
- airtable_state_version: 11
- airtable_sync_status: STATE_SYNC_VERIFIED
