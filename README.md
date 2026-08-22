# FASHI188 Football Analysis Runtime

This repository is the GitHub-only formal persistence source for the football3 project.

It stores runtime code, data/research assets, validation assets, frozen evidence, manifests, model diagnostics, audit outputs and the unique formal CURRENT.

## Authority boundary

- Current user command authorizes side effects.
- Actual GitHub repository / branch / exact HEAD / PR / run / Artifact establishes repository facts.
- Unique formal rule: `football-data/governance/CURRENT_唯一正式规则.md`.
- GPT fact boundary: `football-data/governance/FOOTBALL3_GPT_FACT_GATE_V1.md`.
- Local files, chat, Airtable and historical reports are non-authoritative audit references.

The GitHub worktree must contain exactly one `CURRENT_唯一正式规则.md`. Missing or duplicate candidates fail closed.

## Stable controls

- `AGENTS.md`: GitHub-only authority and authorization boundary.
- `EXECUTION_LITE.md`: execution, retry and remote-verification discipline.
- `governance/validate_project_continuity.py`: deterministic uniqueness and governance-topology guard.
- `football-data/governance/formal_cold_start_candidate_v1_scope.json`: exact branch scope and CURRENT/fact-gate blob anchors.

Formal changes require a GitHub commit SHA and exact-HEAD Actions evidence. Local-only changes are never formal.

Football runtime and evidence live under `football-data/`.
