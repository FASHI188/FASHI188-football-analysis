# Workflow Inventory Summary

- Frozen SHA: `c9311d3c33d37f0ff52774ecfc4a7816209e3a2a`
- Exact workflow count: **411**
- Zero-UNKNOWN gate: **PASS**
- YAML parse failures: **0**
- `contents: write`: **402**
- git commit/push, including one-level helpers: **267**
- push + write: **387**
- push + write + persist: **266**
- direct push to main: **203**
- missing concurrency: **156**
- likely-long without cancel: **103**

## Final disposition counts

- ARCHIVE: **348**
- CONSOLIDATE: **54**
- KEEP: **5**
- MANUAL_ONLY: **4**

## Six recovered workflow paths

- `.github/workflows/football-v6851-multiline-research-readiness.yml`
- `.github/workflows/football-v6852-kambi-formal-domain-coverage.yml`
- `.github/workflows/football-v696-1x2-accuracy-diagnostics.yml`
- `.github/workflows/football-v697-1x2-market-anchor-diagnostic.yml`
- `.github/workflows/football-v699-market-disjoint-validation.yml`
- `.github/workflows/football-workflow-stability-v474.yml`

## Highest-risk workflows

| Workflow | Risk | Target |
| --- | --- | --- |
| `.github/workflows/football-clubelo-direct-readiness-v515.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-clubelo-history-fast-v539.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-clubelo-history-v515.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-clubelo-residual-v515.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-context-intelligence-readiness-v514.yml` | CRITICAL_PUSH_WRITE_PERSIST | `scheduled-data.yml/forward.yml` |
| `.github/workflows/football-cross-year-batch2-v469.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-current-season-batch1-v468.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-external-signal-adjudication-v521.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-fpl-context-challenger-v519.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-fpl-snapshot-readiness-v518.yml` | CRITICAL_PUSH_WRITE_PERSIST | `scheduled-data.yml/forward.yml` |
| `.github/workflows/football-full-chain-diagnostic.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-gdelt-context-coverage-v517.yml` | CRITICAL_PUSH_WRITE_PERSIST | `scheduled-data.yml/forward.yml` |
| `.github/workflows/football-ger-three-surface-dual-v542.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-ger-three-surface-fast-v536.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-ger-three-surface-v534.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-global-evidence-alignment-v475.yml` | CRITICAL_PUSH_WRITE_PERSIST | `scheduled-data.yml/forward.yml` |
| `.github/workflows/football-kalmar-malmo-question-time-audit.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-lineup-latent-signal-v502.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-market-consensus-smoke-v555.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-market-evidence-priority-v525.yml` | CRITICAL_PUSH_WRITE_PERSIST | `scheduled-data.yml/forward.yml` |
| `.github/workflows/football-market-selective-opening-v550.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-market-surface-readiness-all17-v535-diagnostic.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-market-surface-readiness-all17-v535.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-multisource-evidence.yml` | CRITICAL_PUSH_WRITE_PERSIST | `scheduled-data.yml/forward.yml` |
| `.github/workflows/football-orgryte-djurgarden-question-time-audit.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-player-xi-gate-adjudication-v503.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-player-xi-matrix-projection-ita-v504.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-player-xi-readiness-v502.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-player-xi-replication-v503.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-player-xi-residual-signal-v502.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-predictability-gate-v516.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-prospective-market-consensus-v554.yml` | CRITICAL_PUSH_WRITE_PERSIST | `scheduled-data.yml/forward.yml` |
| `.github/workflows/football-prospective-market-matrix-validation-v548.yml` | CRITICAL_PUSH_WRITE_PERSIST | `scheduled-data.yml/forward.yml` |
| `.github/workflows/football-prospective-market-selective-validation-v552.yml` | CRITICAL_PUSH_WRITE_PERSIST | `scheduled-data.yml/forward.yml` |
| `.github/workflows/football-prospective-market-snapshot-v523.yml` | CRITICAL_PUSH_WRITE_PERSIST | `scheduled-data.yml/forward.yml` |
| `.github/workflows/football-recent-xg-forward-v513.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-recent-xg-identity-v512.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-repository-integrity-v471.yml` | CRITICAL_PUSH_WRITE_PERSIST | `ci.yml/maintenance.yml` |
| `.github/workflows/football-retrospective-ah-ou-ceiling-v528.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
| `.github/workflows/football-retrospective-ah-ou-por-sco-parallel-v544.yml` | CRITICAL_PUSH_WRITE_PERSIST | `research.yml` |
