# Football3 New Engine V1 — File Whitelist

Only the following paths may be added or modified by this rebuild branch:

- `football-data/new_engine_v1/**`
- `.github/workflows/football3-new-engine-v1.yml`
- `.github/workflows/football3-new-engine-v1-forward.yml`

Explicitly forbidden writes include, without limitation:
- `CURRENT*`
- `football-data/**/CURRENT*`
- all existing V500/R43 model/core/result paths outside `football-data/new_engine_v1/**`
- R5 paths
- PR #334 metadata/state
- `main`
- Airtable or any external state store

Existing repository files outside the whitelist may be read only when needed for source lineage, identity/PIT utility discovery, or legacy comparison. No merge, Ready action, force push, or formal status update is permitted.

The CI guard must compare the branch against anchor `7c1815c47102412e88f72189e2b8f837d9b73a42` and fail if any changed path is outside this whitelist.