# Football3 Historical XG Fusion V2 — preregistration

Status: `FROZEN_BEFORE_FUSION_SCORING`

## Immutable research parents
- Frozen V1: branch `football3/new-engine-v1`, HEAD `22f639304d2e32fc952dbec2255153ee45dcd41a`, Artifact `9732754224`, digest `sha256:5f0af0c428f19492715669c8e4fb2451ee94bf373f17f5560ff1a42114375bcb`.
- Frozen Historical XG V1: branch `football3/historical-xg-challenger-v1`, HEAD `08a13ce01ddf7c3408b7b89d39d44c01a3b30c9b`, Artifact `9826787329`, digest `sha256:64e9242201006b71265f35a0199d07b1fe1bde1fa68d1c1073310ce40f30ca7e`, terminal status `HISTORICAL_XG_CHALLENGER_REJECTED`.
- The old 2023 confirmation (1,752 matches) is permanently `POST_VIEW_DIAGNOSTIC`; it is forbidden for fusion-weight selection or promotion.
- Frozen XG state parameters are copied exactly from the parent result and MUST NOT change: half-life 90d, prior 4, beta 0.15, cross-season shrink 0.40, min effective evidence 3, pooled prior weight 0.50, residual clip 0.75, xG pseudocount 0.25.

## Only permitted model change
For each match and one globally selected scalar weight `w`:

`p_final = normalize((1-w) * p_V1 + w * p_XG)`

No other probability or score-matrix modification is permitted. No league/team/outcome/draw/context-specific weight.

## Frozen weight grid
`w ∈ {0.25, 0.50, 0.75}` exactly. No interpolation, extrapolation, rescue value, or post-result addition.

Selection uses only the frozen parent candidate-selection period: Big-5 seasons 2018 and 2019, strict historical PIT ordering. V1 and XG predictions must be reproduced from the frozen V1 bytes and frozen XG V1 bytes/parameters.

A weight is selection-feasible only if, versus V1 on the selection period:
1. Brier is not worse (tolerance 1e-12),
2. RPS is not worse (tolerance 1e-12),
3. Top1 delta is at least -0.0015.

Among feasible weights choose the lowest LogLoss; deterministic tie-break: lower Brier, lower RPS, then smaller `w`. If no weight is feasible: terminal `HISTORICAL_XG_FUSION_V2_REJECTED` and do not score outer/confirmation.

## Frozen development outer validation
Use the same parent historical universe and the same 2020–2022 continuous eight-fold outer construction. No random split and no old 2023 confirmation in development.

All must pass:
- at least 6/8 folds have Fusion LogLoss <= V1 LogLoss + 1e-12;
- pooled V1−Fusion LogLoss gain >= 0.001;
- Fusion Brier <= V1 Brier + 1e-12;
- Fusion RPS <= V1 RPS + 1e-12;
- Fusion Top1 − V1 Top1 >= -0.0015;
- every competition×season group with n>=100 has Fusion LogLoss degradation <= 0.020;
- where actual home mean goals > away mean goals, predicted home mean goals must remain > predicted away mean goals;
- all PIT, identity, result-release, duplicate-fixture and same-kickoff predict-before-update guards pass;
- every parent-XG fallback prediction must make Fusion probabilities byte/numerically identical to V1.

Any failed development gate => `HISTORICAL_XG_FUSION_V2_REJECTED`; no new historical confirmation scoring.

## New historical confirmation
Only if every development gate passes. Confirmation must use completed Big-5 season 2024/25 (Understat season key 2024) frozen from public Understat league pages, with expected total n=1,752: EPL 380, La liga 380, Bundesliga 306, Serie A 380, Ligue 1 306.

Before reading confirmation goals for scoring, freeze and seal:
- source page hashes and canonical source-row SHA256;
- exact cohort identities and cohort SHA256;
- proof of zero fixture-ID overlap with the parent 18,084-match 2014–2023 universe;
- selected global `w`;
- V1 prediction SHA256;
- frozen-XG prediction SHA256;
- Fusion final prediction SHA256.

Historical PIT rule: for a target match, its own xG/goals are unavailable until its prediction is frozen and the mechanical +3h release time is reached. Exact same kickoff batch predicts fully before any batch update.

Confirmation gates, all required:
- pooled V1−Fusion LogLoss gain >= 0.001;
- Brier not worse;
- RPS not worse;
- Top1 delta >= -0.0015;
- competition groups with n>=100 LogLoss degradation <= 0.020;
- home-direction invariant passes;
- fallback probabilities equal V1 exactly;
- PIT/identity/batch/source guards pass.

Report draw, underdog-win, cold-start and low-confidence subgroups; these are reports, not tuning targets.

Any confirmation failure => `HISTORICAL_XG_FUSION_V2_REJECTED`.
All confirmation gates pass => `HISTORICAL_XG_FUSION_V2_CANDIDATE_PASSED`.

Regardless of status: `HISTORICAL_PIT_CONFIRMATION`, research-only, `formal_weight=0`, `formal_enablement=false`, no prospective queue and no automatic formal activation.
