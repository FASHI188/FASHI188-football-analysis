# Football3 V1 Error Atlas v1 — Read-Only Diagnostic Contract

Status: READ_ONLY_DIAGNOSTIC / POST_VIEW_DIAGNOSTIC / NOT_PROMOTION_ELIGIBLE

## Frozen identities
- V1 branch/head: `football3/new-engine-v1` / `22f639304d2e32fc952dbec2255153ee45dcd41a`
- V1 Artifact: `9732754224`
- V1 Artifact ZIP SHA256: `5f0af0c428f19492715669c8e4fb2451ee94bf373f17f5560ff1a42114375bcb`
- frozen V1 engine SHA256: `cc2c2c3eca421ad6d277107b8f1212656b2e943cc179e7f394ac53e916c3f318`
- development: 1,826 rows, SHA256 `b8713ceed6d57ead7b2aadbb24d3154bf5cb5df0d45eef0e762b5c395d6d4fab`
- already-unsealed POST_VIEW: 5,256 rows; feature SHA256 `10620687f988ff942d1e56d372f6b7e2721aa65654f9665e982ae27232b472a9`; label SHA256 `e82b612d6e6b974c5f4695f29551830beaec87804429e586cd821aef7e7dff2a`

The 5,256-row carrier Artifact is used only as an immutable data container. Its V2/V2.1 models, locks and predictions are not inputs to this atlas.

## No-model-change rule
The atlas imports the frozen V1 engine bytes and the selected V1 parameters from Artifact 9732754224. It may replay V1 under strict chronological result release to obtain diagnostics, but it must not change probabilities, score matrices, parameters, priors, weights, state rules, or any model code.

Development and POST_VIEW are reported separately. POST_VIEW is explicitly not blind, prospective, confirmation, or promotion eligible.

## Required atlas
Report overall and actual-class conditional LogLoss/Brier/RPS/Top1; class calibration and probability bins; draw probability bias; 0-0/1-1/2-2 predicted frequency versus actual frequency; predicted-total, strength-gap, favourite-confidence, cold-start, and competition×season groups; Top1 confusion; group shares of total LogLoss and non-additive excess-LogLoss contributions.

For error-source comparison, retrospective diagnostic opportunity proxies are allowed only as attribution tools and are never candidate scores. They may not replace or mutate frozen V1 predictions.

## Single recommendation rule
A V1 Joint-Score challenger is recommended only when both development and POST_VIEW show the same-sign draw-frequency gap with absolute magnitude >=0.01, the same-sign aggregate 0-0/1-1/2-2 gap with absolute magnitude >=0.01, and the total-goal-binned draw-structure diagnostic LogLoss opportunity is >=0.001 in each set.

Otherwise a V1.2 multiclass calibration layer is recommended only when mean-class ECE is >=0.02 in each set, at least two of three classes have same-sign calibration bias with absolute magnitude >=0.01 in each set, and the frozen confidence-bin calibration diagnostic LogLoss opportunity is >=0.001 in each set.

If neither rule passes, the only conclusion is `STOP_NO_STABLE_ERROR_TARGET`.

No new model may be started by this workflow.
