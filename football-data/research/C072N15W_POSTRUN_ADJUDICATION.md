# C072-N15W — Post-run adjudication

## Binding terminal
`C072N15W_STRUCTURED_PT_PARK`

Classification: `POST_VIEW_HYPOTHESIS_NEW_DATA_PLAN` development evidence only. Not confirmation.

Authoritative run:
- workflow run `32258846109`
- job `96086932794`
- artifact `football3-c072n15w-aleague-women-structured-pt`
- artifact id `9367403685`
- artifact ZIP SHA256 `cac89e8408430b02873cedf8ab69989eb85c014147799e090a3ee76531580305`

## Boundary result
- exact zero-label eligible inventory reproduced before targets: 59/61/139/135 =394;
- valid scored targets: 388;
- noneligible target cells read=0;
- 2020-2021 file downloaded=false;
- 2025-2026 reserve file downloaded=false;
- 2025-2026 target values read=0;
- model_fit=0;
- C073-C077 scientific results used=false;
- C070-F Confirmation1597/protected remain sealed.

## Frozen comparison
Baseline B0:
- T-1 O/U2.5 only;
- q2.5 de-vig from exchange back/lay mid implied probabilities;
- deterministic 80-iteration bisection solves Poisson lambda with P(T>=3)=q2.5;
- complete P(T=0..6,7+) from that Poisson.

Candidate C:
- T-1 O/U0.5/1.5/2.5/3.5/4.5 cumulative-tail probabilities;
- fixed unweighted non-increasing PAV projection;
- exact bins P0..P4 by adjacent tail differences;
- >=5 mass split into 5/6/7+ using the same B0 q2.5-derived Poisson conditional tail proportions;
- no model fitting, tuned weights, smoothing or calibration.

## Pooled evidence (388 matches)
B0:
- LogLoss `1.8977016509971016`
- Brier `0.8332628092091156`
- RPS `0.13534228488620517`
- Top1 `0.24484536082474226`
- Top3 `0.6185567010309279`

C:
- LogLoss `2.030779820192597`
- Brier `0.8367307022096367`
- RPS `0.13536802412881194`
- Top1 `0.2422680412371134`
- Top3 `0.6005154639175257`

Candidate minus baseline:
- dLogLoss **`+0.1330781691954952`** (worse)
- dBrier `+0.003467893000521194`
- dRPS `+0.000025739242606764856`
- dTop1 `-0.002577319587628857`
- dTop3 `-0.01804123711340211`

Paired bootstrap 5000 seed72018:
- 90% CI **`[+0.00010707000659607116,+0.32483450554994964]`**
- P(delta<0)=`0.0486`.

Season dLogLoss:
- 2021-2022: `-0.027078937737088138` better
- 2022-2023: `+0.03041580377489228` worse
- 2023-2024: `+0.18606289458550584` worse
- 2024-2025: `+0.1942244248901015` worse
=> wins 1/4.

Isotonic pooling was rare: 8/388 (2.06%), mean max tail adjustment ~0.00107. Therefore the failure is not mainly caused by non-monotone cross-line quotes.

## Scientific interpretation
N15W rules out this exact zero-parameter interpretation of the five O/U lines as a complete exact-T distribution with q2.5-Poisson high-tail closure on this new development domain. The failure is not attributable to N13's linear-model collinearity because N15W fits no predictive model.

The much larger LogLoss deterioration than RPS deterioration indicates the structured candidate occasionally assigns severely inadequate probability to the realized exact-T class while preserving much of the ordinal/cumulative mass. This is evidence that bookmaker/exchange half-goal line probabilities should not be treated as mutually coherent exact-bin probabilities without an independently justified noise/measurement model.

N15W development labels are now globally consumed. No alternate isotonic weighting, smoothing, tail family, line subset, time weighting or blend may be tried on these labels and called a continuation of N15W.

## Hard stopping boundary
- 2025-2026 A-League Women reserve 116 stays target-sealed because N15W did not development-PASS.
- Men's A-League 2025-2026 reserve remains sealed as well.
- C070-F Confirmation1597 remains sealed.
- C073-C077 remain scientifically quarantined.
- formal_weight=0.
