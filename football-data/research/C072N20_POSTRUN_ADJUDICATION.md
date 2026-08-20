# C072-N20 — post-run adjudication

## Binding terminal
`C072N20_P1000_PILOT_NO_SIGNAL`

Classification: `POST-VIEW DEVELOPMENT / REPLICATION PILOT`; not fresh, blind, pristine, independent confirmation or formal promotion. `formal_weight=0`.

## Immutable cohort and label boundary
- exact N20 rows: 1000.
- ordered identity SHA: `a49e61df94d0f9c368b314829901f0d64d69ad25c51813551a298307e15e56cf`.
- source counts: BR318 / GR121 / MLS313 / TR248.
- overlap prior football3 N16 selected2000: 0.
- overlap explicitly excluded cross-project C079 consumed1000: 0.
- exact target join: 1000/1000; identity mismatches: 0.
- FTHG/FTAG values materialized: 2000.
- the N20 1000 are globally consumed and may not be reused to shop a neighboring representation.
- C070-F1597 / N17 reserve266 / N18 confirmation150 remained unopened.
- C073-C077 scientific results used: false.

## Engineering precursor and replay
First label-access run `32326379971` / job `96298363218` reached exact 1000/1000 join but failed before any scientific score because `test.T` invoked pandas DataFrame transpose instead of column `T`.

Correction `C072N20_EXECUTION_CORRECTION_01.md` authorized exactly one textual substitution to `test['T']`. No scientific contract field changed.

Authoritative engineering replay:
- run `32326652798`
- job `96299173818`
- HEAD `b067f49972e0522512a5cbca5acc1576cd2f112f`
- artifact `9391656598`
- artifact digest `sha256:80d4e65a5c69e88ad6e71dff8f22cf988eefeb31849a2b6bebcd773c8e813db3`
- workflow conclusion: SUCCESS.

## Frozen comparison
B0: line-calibrated O/U2.5 tail -> Poisson full-support -> classes 0..6,7+.

C: five independently line-calibrated O/U tails -> equal-weight decreasing PAVA -> exact p0..p4 plus P(T>=5), with >=6/>=7 continuation ratios inherited from the same B0 Poisson anchor.

Training/calibration uses only the already-consumed N17 development 1734. No N17 reserve access.

## Pooled exact-T evidence
Baseline:
- LogLoss `1.8156748128`
- Brier `0.8154681826`
- RPS `0.1219649878`
- Top1 `24.0%`
- Top3 `69.1%`
- T=2 Top1 fraction `78.0%`

Candidate:
- LogLoss `1.8198346208`
- Brier `0.8163667784`
- RPS `0.1221441330`
- Top1 `24.1%`
- Top3 `68.0%`
- T=2 Top1 fraction `62.7%`

Candidate minus baseline:
- dLogLoss `+0.0041598079` — worse
- dBrier `+0.0008985958` — worse
- dRPS `+0.0001791452` — worse
- dTop1 `+0.10pp`
- dTop3 `-1.10pp`
- T=2 mode fraction `-15.3pp`

Paired bootstrap 5000 seed72020:
- 90% CI dLogLoss `[-0.0003677775,+0.0086930374]`
- bootstrap mean `+0.0042013614`
- P(dLogLoss<0) `0.0662`.

Per-source dLogLoss:
- BR `+0.00345075`
- GR `+0.01458747`
- MLS `+0.01118478`
- TR `-0.00888488`
- wins: `1/4`.

Probability conservation maximum residual: `1.11e-16`.

## Gate adjudication
Failed required gates:
- pooled dLogLoss < 0: FAIL
- bootstrap90 upper < 0: FAIL
- dBrier <= 0: FAIL
- dRPS <= 0: FAIL
- >=3/4 source wins: FAIL (1/4)

Passed:
- exact 1000 join
- probability conservation
- sealed boundaries.

Therefore terminal is `C072N20_P1000_PILOT_NO_SIGNAL`.

## Scientific interpretation
N20 did one useful mechanical thing: it materially reduced the pathological concentration of the total-goal mode at T=2 (78.0% -> 62.7%). But this redistribution was not better calibrated to realized T: all three proper-score families worsened and only one of four sources improved LogLoss.

This is important negative evidence. **Reducing mode concentration is not sufficient; a P(T) repair must move probability mass in the right match-specific directions, not merely flatten or diversify the mode.**

The exact N20 representation — independent line calibration + equal-weight PAVA + Poisson tail continuation — is PARKed. The result does not prove the broader latent-market-measurement idea impossible; it does rule out rescuing this exact implementation on these 1000 labels.

## Hard next-step boundary
Forbidden on the N20 1000:
- change calibration C/family;
- change PAVA weights/projection;
- change tail continuation;
- drop/add O/U lines;
- source/season filtering;
- thresholding or Top1 optimization;
- changing baseline, metrics, bootstrap or gates;
- Draw/0-0/1-1/T=2 manual adjustment.

Any next football3 experiment must be a materially new P(T) hypothesis with a new preregistered data plan. No sealed reserve may be used to rescue N20.
