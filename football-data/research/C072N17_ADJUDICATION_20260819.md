# C072-N17 adjudication — 2026-08-19

## Authority

- project: `football3`
- PR: `#306`
- frozen parent: `football3/c072n16r1-new2000-footiqo-protocol-correction-20260819` @ `271578d8f9e33a5350b8b8c36bf03423a64de41d`
- authoritative replay head: `aaf9567528cdd6d0313dd49dd9f2aa5d8615a4b0`
- authoritative workflow run: `32265869790`
- authoritative job: `96110096649`
- artifact: `9370213104`
- artifact digest: `sha256:a25aede91e531767b6c09d3e75933dddde6b95ab8c794dee380e5410b04c45b9`
- terminal: `C072N17_HDA_INCREMENT_PT_DEVELOPMENT_PARK`
- evidence class: **DEVELOPMENT / REPLICATION, NOT CONFIRMATION**

The prior run `32265308160` / job `96108223180` completed the frozen scientific calculation but failed only on JSON serialization. The authoritative replay uses a serialization-only wrapper; the frozen scientific runner was not changed. Therefore the authoritative replay is an execution reproduction of the same frozen N17 experiment, not new evidence.

## Boundary audit

- exact N16R1 input rows: 2,000
- authorized development identities: 1,734
- target join rows: 1,734
- target join coverage: 100%
- identity mismatches: 0
- duplicate sourceCode+id result rows: 0
- later reserve identities: 266
- later reserve target values materialized: 0
- non-selected target values decoded: 0
- C073-C077 scientific results used: false
- C070-F Confirmation1597 opened: false
- A-League men/women 2025/26 reserves opened: false

The 1,734 N17 development labels are now consumed. The 266 later-reserve labels remain unread and must not be spent on an N17 confirmation because N17 failed its development contract.

## Frozen comparison

Baseline:
- five closing de-vig O/U 0.5/1.5/2.5/3.5/4.5 logits
- league one-hot
- median imputer + StandardScaler
- multinomial LogisticRegression C=0.1

Candidate = baseline plus exactly:
- `hda_gap = log(pH/pA)`
- `hda_draw = log(pD/sqrt(pH*pA))`

No BTTS, movement, score-history, interactions, feature subset search, alternate C or alternate model was used.

## Pooled rolling-OOS result

Pooled OOS n = **941**.

Baseline:
- LogLoss: `1.848751011707609`
- Brier: `0.8251642187906112`
- RPS: `0.1250904788079279`
- Top1: `0.24017003188097769`
- Top3: `0.6333687566418703`

Candidate:
- LogLoss: `1.851686745365468`
- Brier: `0.8255037110392293`
- RPS: `0.12519287267033116`
- Top1: `0.2507970244420829`
- Top3: `0.6397449521785334`

Candidate minus baseline:
- dLogLoss: **`+0.002935733657859041`** (worse)
- dBrier: **`+0.00033949224861806737`** (worse)
- dRPS: **`+0.00010239386240326609`** (worse)
- dTop1: `+0.01062699256110522`
- dTop3: `+0.006376195536663132`

The Top1/Top3 gains cannot override the proper-score deterioration.

## Bootstrap

Paired match bootstrap, 3,000 resamples, seed 72017:
- mean dLogLoss: `+0.002879467655155203`
- 90% CI: **[`-0.0031245758479849744`, `+0.008807338781805986`]**
- P(dLogLoss < 0): `0.21666666666666667`

The interval crosses zero and its center is adverse.

## Time-fold stability

LogLoss candidate-minus-baseline:
- 2020: `+0.0033334798933692955`
- 2021: `+0.0015765838936303567`
- 2022: `+0.0073458628051650265`
- 2023: `-0.0017632385248549376`
- 2024: `+0.004728579720238102`

Only **1/5** chronological folds improved; frozen gate required >=4/5.

## League stability

LogLoss candidate-minus-baseline:
- BR: `+0.007034412397834533`
- GR: `-0.002353451464565337`
- MLS: `+0.008493311227168343`
- TR: `-0.005229818912385831`

Only **2/4** leagues improved; frozen gate required >=3/4.

## Gate adjudication

Failed:
- pooled dLogLoss < 0
- bootstrap 90% upper < 0
- Brier non-worse
- RPS non-worse
- >=4/5 fold LogLoss wins
- >=3/4 league LogLoss wins

Passed:
- probability conservation (`4.440892098500626e-16` max residual)
- reserve targets remain zero-read

Final adjudication: **PARK**.

## Scientific interpretation

Closing 1X2 composition can move exact-T classification ranks enough to improve Top1/Top3 on this development pool, but it does **not** provide stable proper-score information beyond the five-line closing O/U surface under the frozen linear multinomial representation. The adverse pooled LogLoss/Brier/RPS, only 1/5 temporal wins, and opposite-sign league effects are direct counterevidence to promoting this representation.

No N17 feature/model/regularization/fold/gate repair may be attempted on these consumed labels. Any future football3 attempt must be a materially new, preregistered information hypothesis with a separately audited data plan.