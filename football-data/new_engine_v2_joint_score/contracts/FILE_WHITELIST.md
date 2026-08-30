# V2 File Whitelist

Status: FROZEN BEFORE IMPLEMENTATION

Only these paths may differ from anchor `7c1815c47102412e88f72189e2b8f837d9b73a42` on `football3/new-engine-v2-joint-score`:

- `football-data/new_engine_v2_joint_score/**`
- `.github/workflows/football3-new-engine-v2-joint-score.yml`
- `.github/workflows/football3-new-engine-v2-joint-score-forward.yml`

The contract-only first commit itself may contain only:
`football-data/new_engine_v2_joint_score/contracts/*.md`

Any other changed path is an immediate `STOPPED_GOVERNANCE`. It is not acceptable to add an out-of-whitelist path and delete it in a later commit; every commit from anchor to HEAD must independently satisfy the whitelist. No main/CURRENT/Airtable/PR/R5/formal-model mutation is authorized.