# 足球项目当前状态

## 身份

- project_id: football-project
- state_version: 13
- updated_at_utc: 2026-08-13T03:46:58Z
- updated_by: GPT-5.6 Sol
- status_source: VERIFIED_GITHUB_AIRTABLE_FILE_LIBRARY_RESEARCH
- project_current_sha256: RECORDED_IN_AIRTABLE

## 当前状态

- status: WAITING
- current_phase: R45A_RETROSPECTIVE_PLAYER_CAPABILITY_300_COMPLETE
- current_objective: 用户从上传的 `archive (1).zip` 固定选择五大联赛各60场共300场，要求去除意志力并测试赛前全景基础层与球员战斗力。R45A已完成回顾性研究：市场原生300/300；外部历史身份匹配299/300；训练4232场；双方首发球员属性均值约10.97/11，team style 299/299。加入原始球员绝对能力汇总后，平局Top-1从M3的5场增至M4的9场，但只命中3场，且proper scores显著恶化，因此该原始球员能力层拒绝晋级。
- exact_head: 39450499a501bad1fb5671812f89eb18379b99bf
- branch: research/r45a-player-capability-300-retro
- pull_request: NONE
- pull_request_state: NONE
- formal_weight: 0
- latest_execution_head: d2ce8d5f463a896b50aa9b190e2e73b6b4edc35d
- latest_execution_run: 31664611305
- latest_execution_job: 94336376824
- latest_execution_artifact: 9167483574
- latest_execution_artifact_sha256: b7607aed68ece159420d44a5c844b40dd82181c9adb4530735cb12437febab36
- latest_execution_verdict: R45A_RETROSPECTIVE_COMPLETE_RAW_PLAYER_LAYER_NEGATIVE_INCREMENT
- scientific_effect: RETROSPECTIVE_NEGATIVE_INCREMENT_NOT_PROMOTION_EVIDENCE

## 当前证据

- current_rule: 唯一正式CURRENT=`足球项目_CURRENT_唯一正式规则_V5.2.0_PIT数据优先与尾部闭合统一晋级版.docx`；数量=1；本对话已完整读取并应用；formal_weight未改变。
- fixed_sample: 用户上传archive固定300场；五大联赛各60；选择使用身份/日期固定哈希，不按赛果重抽。fixed sample sha256=`bc58fcf4bf4a452d1769dacd9111b1cdf88e5039a45e706d339559a3627f163b`。
- market_layer: archive原生末端完整H/D/A去水聚合；300/300覆盖。完整300场实际H=132/D=78/A=90；市场Top-1平局=0。进入共同299场后M0 LogLoss=0.952316、accuracy=53.85%、draw AUC=0.595238。
- identity_and_coverage: 外部历史源commit=`ec4994d56d4f1d35412514bdca9dedd258341a14`；299/300匹配；唯一未匹配=`2016-05-19 Eintracht Frankfurt vs Nurnberg`；train=4232；test=299；home XI player snapshots mean=10.9666，away=10.9699；both XI >=9为299/299；team style complete=299/299。
- user_exclusion: 明确不使用“意志力”；同时R45A排除aggression、attacking_work_rate、defensive_work_rate、potential，避免用其替代意志力。
- model_variants: M0=archive native market；M1=market calibration；M2=market+rolling team history；M3=M2+team tactical style；M4=M3+player capability。固定L2 Logistic、C=1.0、lbfgs、max_iter=2000；无调参、无阈值搜索。
- player_capability: overall、pace、dribble/control/agility、passing/vision、shooting/finishing、physical、defense、setpiece、reactions、stamina、GK，以比赛日前最近属性快照和历史首发XI汇总。
- metrics: M2 LogLoss=0.951487 accuracy=54.18% drawTop1=1/0中；M3 LogLoss=0.958139 accuracy=53.85% drawTop1=5/1中；M4 LogLoss=0.969794 accuracy=53.51% drawTop1=9/3中，draw precision=33.33%，draw recall=3.90%，draw AUC=0.585761。
- bootstrap: 10,000次配对bootstrap seed=20260813。M4-M3 delta LogLoss=+0.011655，90%CI=[+0.002937,+0.020333]；M4-M2=+0.018307，90%CI=[+0.007282,+0.029432]；M4-M0=+0.017478，90%CI=[+0.003427,+0.032001]。三个区间均完全>0，表示M4稳定恶化而非随机噪声。
- domain_consistency: M4-M3 LogLoss在英超、法甲、德甲、意甲、西甲五域全部为正（全部恶化），不是单一联赛拖累。
- ruling: 增加原始绝对球员能力汇总确实让模型更常把平局推到Top-1，但6/9为误报，并同时恶化LogLoss/Brier/RPS/AUC，因此“更敢判平局”不是成功证据。该M4不得晋级，不得非零formal_weight。
- scope_limit: 本结果只否定当前“无条件绝对球员能力汇总”实现，不否定球员信息本身。角色可用性、替代者差值、职责变化及与对手的matchup interaction仍是不同假设，但不得在本299场事后调出结果后冒充独立确认。
- pit_limit: 历史首发XI与FIFA式球员快照缺少可核验赛前available_at，状态固定=`RETROSPECTIVE_LINEUP_AND_FIFA_SNAPSHOTS_PIT_UNVERIFIED`，不能直接进入正式live赛前链。
- unavailable_not_fabricated: 本轮没有可核验PIT资产支持历史伤停、新闻/发布会、天气、裁判、完整比赛任务效用、xG/事件过程，因此这些“赛前全景”层未人工补造，也不能声称本轮已全部测试。
- engineering: 前两次Actions仅在fit前因gzip传输与pyreadr日期=1970问题停止；最终只实施RData日期读取器修复，`model_feature_parameter_changes=0`。original runner sha256=`e4bce5f43ba3a6db80ff6e5c9386249d05f59fc61499dd052ca5180e63627ebe`；patched loader-only runner sha256=`b9e03068f429584b4e9c0906fe944ce6a2a004303105e8d1f9d2e17abb444b91`。
- artifact_integrity: result.json sha256=`7a185494292e2003984734872da663d3564f0e9a099e8a75a257a822279bfd05`；predictions.csv sha256=`2d30050df4ebe7047f3a2850cc3d8987af3afc59d95d38030a6d5efb02e2055f`；研究结果记录HEAD=`39450499a501bad1fb5671812f89eb18379b99bf`。
- formal_changes: model=0；formal data=0；config=0；CURRENT=0；formal_weight=0。
- evidence_observed_at: 2026-08-13T11:46:58+08:00

## 允许事项

- 只读复盘R45A run/job/Artifact、固定样本、逐场预测和结果记录。
- 在不打开新盲标签的前提下，为“赛前全方位判断”建立新的零标签PIT输入轴：预计/确认首发available_at、伤停/停赛、球员角色和替代差、xG/过程能力、任务效用、同步市场及其冻结变化。
- 对用户archive已查看的历史结果可继续做明确标注为VIEWED/RETROSPECTIVE的描述性审计，但不得冒充新的盲确认。

## 禁止事项

- 禁止把M4的9个平局Top-1或3个命中写成“平局问题解决”。
- 禁止在R45A同299场上事后调球员评分组合、筛特征、改C/窗口/阈值，再称为独立确认或晋级证据。
- 禁止给R45A/M4非零formal_weight。
- 禁止把历史首发XI/FIFA快照写成已验证的赛前PIT正式输入。
- 禁止把本轮未具备PIT资产的伤停、新闻、天气、裁判、任务效用、xG层写成“已经算进去”。
- 禁止修改正式模型、正式数据、config或CURRENT，除非用户另行明确授权并通过V5.2.0晋级门。

## 唯一下一步

- 停止当前“原始绝对球员战斗力汇总”路线在同样本上的继续调参。
- 若继续赛前全景研究，先零标签建立可核验PIT的球员角色可用性/替代差/对位交互，以及真正赛前available_at的伤停、预计/确认首发、xG/过程能力、任务效用和同步市场输入；冻结数据身份、算法与trainability门后，再由独立/未消费样本验证增量。

## 停止条件

- 任何方案试图在本299场已查看标签上继续选球员特征/交互并将结果称为新的blind/independent：立即停止。
- 新赛前信息无法证明freeze time前available_at或无法重建严格PIT：只能研究/描述，不得进入正式链。
- 涉及新盲标签、正式训练/评分/晋级/CURRENT或正式资产修改时必须重新获得相应明确授权。
- PROJECT_CURRENT与Airtable current state_version/HEAD/唯一下一步/绑定日志不一致：`BLOCKED_STATE_MISMATCH`。

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
- state_log_record_id: recOAtiyGCVi2svXq
- airtable_state_version: 13
- airtable_sync_status: STATE_SYNC_PENDING_GITHUB_WRITE
