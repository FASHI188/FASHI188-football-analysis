# Football3 CURRENT V2 Retrospective Replay v1

## Construction binding

- Repository: `FASHI188/FASHI188-football-analysis`
- Canonical integration base: `football3/formal-gpt-runner-integration-v1`
- Exact construction base: `0e102ac3689185d3378bab3bc416ccec20d519de`
- Live main at construction start: `2b74066607fa8fe64dadb6bf4815aba22bc52ce7`
- Construction branch: `football3/current-v2-retrospective-replay-v1`
- Stage: `WEIGHT_SEMANTICS_VERIFIED_IMPLEMENTATION_IN_PROGRESS`

## Formal model baseline

The canonical formal pointer is `football-data/config/formal_model_pointer_historical_xg_fusion_v2.json` and declares:

- formal model: `Historical XG Fusion V2`
- authority: `EXTERNAL_UNIQUE_CURRENT_ONLY`
- CURRENT is not stored in GitHub and must select the exact formal head at runtime
- formal fusion formula: `normalize((1-w)*p_V1 + w*p_XG)`
- formal `xg_weight`: `0.75`
- formal `frozen_v1_weight`: `0.25`
- xG-insufficient route: `FROZEN_V1_EXACT_FALLBACK`

The immutable formal wiring contract independently declares the same formula and weights, and `formal_fusion_v2.py` defines `FUSION_WEIGHT = 0.75` with `w` being the Historical XG component. Therefore the correct semantic reading is **Historical XG 0.75 + Frozen V1 0.25**. The initial construction note had these component labels reversed; that documentation error is corrected here. No formal configuration, pointer, model code, or weight was changed.

The implementation MUST resolve the actual CURRENT identity and formal pointer dynamically at runtime and record the actual CURRENT SHA/model/runtime identity and mechanically resolved fusion weights in each final Receipt. This feature MUST NOT introduce a new hard-coded CURRENT SHA, model HEAD, runtime HEAD, fusion weight, or route.

## Product contract

New request mode: `CURRENT_V2_RETROSPECTIVE_REPLAY`.

This is a current-formal-model retrospective research replay, not a forensic restoration of the historical production state. Required labels/claims:

- `RETROSPECTIVE`
- `RESEARCH_ONLY`
- `CURRENT_V2_RETROSPECTIVE_REPLAY`
- `NOT_ELIGIBLE_FOR_FORMAL_WIN_RATE`
- `NOT_ELIGIBLE_FOR_PROSPECTIVE_OOS`
- `strict_pit_claimed=false`

History is reconstructed only from fixtures/events strictly before the target fixture kickoff. The target fixture, target result/events, post-kickoff evidence, and target-result-derived state are excluded. Same-kickoff/release-order batches follow predict-all-before-update semantics. Observation timestamps later than the historical target cutoff do not by themselves invalidate this research-only mode; STRICT_PIT behavior remains unchanged.

## Frozen boundaries

This feature MUST NOT change model parameters, CURRENT, formal model pointer, 75/25 weights, evidence thresholds, prospective semantics, STRICT_PIT semantics, PR #341, PR #369, production activation, Airtable, or merge/readiness state.

UCL support, if required, is restricted to a governed research-only entry for this mode and must reuse the existing UCL identity/state authorities without lowering identity/state gates or widening other formal modes.

## Required acceptance

- request/schema tests
- CURRENT V2 retrospective replay tests
- eight-domain batch from `2026-09-01T00:00:00Z`
- permanent no-label regressions including Udinese–Lazio, Hamburg–Mainz, Schalke 04–Bayern Munich, NFO–Tottenham
- prospective formal prediction unchanged
- STRICT_PIT sealed replay unchanged
- request SHA binding and auto-dispatch schema
- seven-domain and UCL identity/state gates
- two independent deterministic replay validations
- Full Stack Scientific Preflight
- Governed Permanent Regression
- production-300 unchanged, including late-settlement fail-closed
- Research Policy
- Quality/Security
- exact-HEAD artifact/digest verification
