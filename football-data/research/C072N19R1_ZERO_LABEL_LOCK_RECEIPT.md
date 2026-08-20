# C072-N19R1 — immutable zero-label lock receipt

Project: football3 only.

Authoritative zero-label workflow:
- run `32323875159`
- artifact `football3-c072n19r1-zero-label-1dc741c6ce76ec42f0d13463feebac69041aa348-32323875159`
- artifact id `9390665850`
- artifact digest `sha256:65e2d8e9980ab5c25609b207735cb3002a2c1d883ed4d5500a400f7a51a217e7`

Frozen source:
- mirror `MestreAlex/elo-rating`
- revision `383d5277fdaed48fd2d909e073e047350e71cb7f`
- fixed source codes `E0,E1,D1,D2,I1,I2,F1,F2,SP1,SP2`

Zero-label result:
- eligible market rows: `2043`
- selected identities: **`1000`**
- ordered identity SHA256: **`10f77a6b20502813c0ae8402c7dd45e80054dcc5b6b6546751fca736033cddce`**
- first locked match date: `2025-08-01`
- last locked match date: `2025-10-30`
- result/score columns materialized: `0`
- target values materialized: `0`
- model fits: `0`

Selected source counts:
- D1 72
- D2 90
- E0 90
- E1 143
- F1 90
- F2 107
- I1 89
- I2 99
- SP1 100
- SP2 120

This receipt is binding. Any target-opening evaluator must first reproduce exactly the same 1000 ordered identities and SHA before materializing any `FTHG`/`FTAG` value. Only those frozen source-row identities may expose score values.

Evidence classification remains `REPLICATION / REPRODUCTION ONLY`; never fresh confirmation. `formal_weight=0`.

Sealed boundaries at lock time:
- C070-F Confirmation1597 unopened;
- N17 reserve266 unopened;
- N18 confirmation150 unopened;
- C073-C077 scientific results not used for N19R1 design/tuning/stopping.
