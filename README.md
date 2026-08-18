# FASHI188 Football Analysis Runtime

This repository is dedicated exclusively to the football-analysis project.

It stores football runtime code, data/research assets, validation assets, frozen evidence, manifests, model diagnostics and audit outputs. Repository visibility is controlled by GitHub settings; this README does not assert a private/public state.

## Authority boundary

- Dynamic construction state: Airtable《当前状态》unique active record only.
- Current user command: required for side-effect authorization.
- Repository facts: actual GitHub branch / HEAD / PR / run / Artifact.
- Formal scientific rules: the unique project-scoped file whose name contains `CURRENT_唯一正式规则`, read only when the task requires formal scientific rules.
- Historical logs, Git history, old PRs and retired documents: evidence only; they do not select the current task or grant authorization.

GitHub must not host a second dynamic project-state mirror or a substitute formal CURRENT.

## Stable repository control files

- `AGENTS.md`: stable authority and governance boundaries.
- `EXECUTION_LITE.md`: execution-load, side-effect and retry safety.
- `governance/validate_project_continuity.py`: static guard that rejects reintroduction of the retired root continuity mirrors.

Old root checkpoint/handoff/current/start-here/plan files are intentionally removed from the working tree. Historical content remains recoverable through Git history and Airtable《维护日志》 when specifically needed.

Football runtime and evidence live under `football-data/`.
