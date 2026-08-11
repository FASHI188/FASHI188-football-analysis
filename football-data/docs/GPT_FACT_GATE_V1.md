# GPT事实核验硬门 V1

## 目的

本规则只解决一件事：防止GPT把“别人报告的状态、排队画面、Workflow成功、Artifact存在、局部指标或推断”写成已经独立核实的项目事实。

本规则不训练模型、不访问Provider、不读取密钥、不修改正式数据、不改变正式权重，也不自动授权研究、晋级、转Ready或合并。

## 四类声明

任何项目回报都必须逐条归入且只能归入以下一种：

1. `VERIFIED`：由实时API、本地确定性复算或哈希绑定Artifact直接支持，并绑定准确HEAD和观察时间。
2. `REPORTED_NOT_VERIFIED`：来自其他对话、人工回报或粘贴文本，尚未独立核实。
3. `INFERRED`：由已知事实推导的解释或方向判断，必须写明依据和不确定性。
4. `UNKNOWN`：证据缺失、冲突、过期、未执行或无法区分时的唯一合法状态。

不得使用“基本确认”“应该通过”“大概率已完成”等中间词绕过分类。

## 硬边界

- 准确HEAD、PR状态、run、job和Artifact必须属于同一身份链；任一不一致即`FAIL_CLOSED`。
- 动态状态必须带`observed_at_utc`，默认超过24小时视为过期，不能继续写成当前事实。
- `success`只表示对应Workflow或job执行结论，不等于科学效果门通过。
- `skipped`必须原样报告为`skipped`，不得写成已执行或已验证。
- Artifact存在只证明文件被上传；只有其HEAD、run和SHA-256一致时才能证明Artifact身份。
- 科学效果`PASS`必须同时具备完整实验状态、受信来源和指标；没有指标不得写`PASS`。
- 盲样本访问数、授权状态和首次访问时间必须相容；未测得精确时间只能写`UNPROVEN_NOT_MEASURED`。
- 转述和推断不得触发训练、研究、Provider访问、正式晋级、Ready或合并。
- 所有输出由同一结构化证据确定性生成；直接修改JSON或Markdown会在复核时失败。
- 缺字段、冲突、额外Artifact文件、哈希不一致或状态过期时一律停止，不补猜。

## 使用方法

实时采集公开GitHub对象：

```text
python football-data/validation/gpt_fact_gate_v1.py collect-github --repo OWNER/REPO --pr PR_NUMBER --run-id RUN_ID --output evidence.json
```

生成报告：

```text
python football-data/validation/gpt_fact_gate_v1.py build --input evidence.json --output-dir fact-report
```

再次确定性复核：

```text
python football-data/validation/gpt_fact_gate_v1.py verify --input evidence.json --output-dir fact-report
```

输出固定为：

- `gpt_fact_report.json`
- `gpt_fact_report.md`
- `gpt_fact_verification.json`
- `manifest.json`

## 对GPT的固定执行要求

每次开始足球项目任务时，先读取本规则。每次准备汇报“已完成、已通过、正在运行、已停止、已生成Artifact、指标改善、问题解决、允许启动或允许合并”之前，必须先通过本硬门；未通过时只能报告`REPORTED_NOT_VERIFIED`、`INFERRED`或`UNKNOWN`，不得改写成确定事实。

本硬门通过也只证明声明与证据边界一致，不证明模型效果更好，不自动授予任何执行权限。
