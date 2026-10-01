# R24 mature-history disjoint confirmation

Status: research-only; no formal promotion; formal_weight=0.

## Candidate
Frozen R21 probability-only draw mechanism model:
- 5-class subtype: non-draw / 0-0 / 1-1 / 2-2 / 3-3+
- strict-prior team/league history
- empirical-Bayes game-state transition features: lead->draw, trail->draw, HT-tie->draw, HT0-0->draw, HT0-0->0-0 and interaction cycles
- closing 1X2 market remains baseline/prior
- 68 frozen features and unchanged R21 hyperparameters

## R24 preregistration
- test window: 2016-07-30 through 2016-09-09
- history before window: 30,395 eligible matches
- every match_id previously present in any local prior `*_predictions.csv` was excluded before selection
- remaining candidate pool: 4,060
- label-free deterministic selection: SHA256(`20260813:R24:<match_id>`) ascending, first 300
- test_n: 300
- no retuning on R24
- prereg SHA-256: `d1a4b04c75e3e7deb41929a17e05fe1ebf9be2d6b91a5c3dd49eaea0eee4ffff`

## Result
- baseline H/D/A LogLoss: 0.9624433409470008
- candidate H/D/A LogLoss: 0.9536334972727595
- delta candidate-baseline: **-0.008809843674241313**
- baseline draw LogLoss: 0.5189300874859588
- candidate draw LogLoss: 0.5101202438117177
- draw LogLoss delta: **-0.008809843674241091**
- baseline draw AUC: 0.565302782324059
- candidate draw AUC: 0.5946972176759411
- AUC delta: **+0.02939443535188213**
- 90% calendar-date block bootstrap CI for HDA LogLoss delta: **[-0.017335056366209127, -0.0015384312772040963]**
- preregistered core gate: **PASS**

Direction diagnostics only:
- market baseline accuracy: 57.0%
- candidate argmax accuracy: 57.0%
- baseline Top-1 Draw: 0
- candidate Top-1 Draw: 1, hit 0

Interpretation: the replicated gain is currently probability quality/ranking, not yet reliable Top-1 draw execution.

## Integrity hashes
- script SHA-256: `850ee55ce7202fa504834d303ac8ccc3c604af14b3504cc5dd9f393ebd24eddf`
- prereg SHA-256: `d1a4b04c75e3e7deb41929a17e05fe1ebf9be2d6b91a5c3dd49eaea0eee4ffff`
- result SHA-256: `3013fff16a325d0fb61df2c7fb01bb762d6f1df75b0ae37cd42ccc6db386da80`
- row-level 300 prediction CSV SHA-256: `1baa71732065a82f009eabfce53d7ca9226186d1ec3f0d958cb85a12eab7a1d2`

R23 earlier-history confirmation (8,728 history matches) failed and is not hidden: LL delta +0.0030867, AUC delta -0.00873, CI crossed zero. This motivates only a new post-R23 hypothesis that transition features need mature historical support; R24 is the first disjoint test of that hypothesis and passes. Further untouched confirmation is still required before promotion.