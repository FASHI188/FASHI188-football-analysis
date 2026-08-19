# C072-N9R1 — Development-only zero-label join correction

## Lineage / classification
- Project: football3 only.
- Parent: C072-N9 head `244b3df7c8f8fcf95e8d44c45ead5062b3ce768e`.
- Original N9 remains `C072N9_ZERO_LABEL_JOIN_STOP`; this contract does not retroactively modify that terminal.
- This is an engineering/governance zero-label correction, not a scientific confirmation.
- C073-C077 remain quarantined and are not ancestry/evidence/design input.

## Purpose
Determine whether the already-frozen N9 identity mapping is sufficient for the preregistered multi-line `P(T)` **development** experiment once the invalid 2024/25 confirmation role is removed.

N9R1 is deliberately forbidden from changing the N9 matching algorithm. It may only subset the exact existing N9 manifest to the fixed development-history seasons.

## Immutable inputs
1. N8 authoritative zero-label odds CSV from workflow run `32244931845`, artifact `football3-c072n8-multiline-odds-zero-label`:
   - CSV SHA256 `e9538997e1ec46582e240add8eb37372341a0c75b51e024c8bef0139aa29c082`.
2. N9 authoritative zero-label join artifact from workflow run `32246282879`, artifact `football3-c072n9-zero-label-join`:
   - manifest SHA256 `027d7f4ff7cb72724115a731002d4d2048f10da420fdb8ac4955eb2782ba3a06`.

No new source request, no rematching, no alternate alias algorithm, and no outcome value access is allowed in N9R1.

## Fixed development domain
Development history seasons:
- 2015/2016
- 2016/2017
- 2017/2018
- 2018/2019
- 2019/2020
- 2020/2021
- 2021/2022
- 2022/2023
- 2023/2024

Frozen rolling OOS test seasons remain exactly:
- 2019/2020
- 2020/2021
- 2021/2022
- 2022/2023
- 2023/2024

Each future development fold must train only on strictly earlier seasons.

## Explicitly excluded domains
- 2024/2025 is **not** a confirmation domain for this experiment. Its target labels must not be opened by N9R1 or the subsequent development run.
- 2025/2026 remains zero-label reserve.
- Any future independent confirmation requires a separate preregistered source/identity contract and a global-consumption audit proving that the target labels are not globally consumed.

## N9R1 zero-label PASS gate
Using only the immutable N8 CSV plus immutable N9 manifest, `C072N9R1_DEVELOPMENT_JOIN_PASS` requires ALL:
1. N8 CSV SHA matches exactly;
2. N9 manifest SHA matches exactly;
3. no forbidden score/result field is present in either zero-label artifact;
4. development-history join coverage across 2015/16–2023/24 >=97%;
5. each fixed source league development-history join coverage >=95%;
6. each frozen rolling OOS season 2019/20–2023/24 join coverage >=95%;
7. each development-history season 2015/16–2023/24 join coverage >=95%;
8. no duplicate accepted N8 row assignment in the development manifest;
9. no duplicate label-source assignment in the development manifest;
10. 2024/25 rows are excluded from development authorization and their target values remain unread;
11. 2025/26 rows remain zero-label reserve;
12. target/result values materialized=0;
13. model_fit=0 and model_score=0.

If any gate fails: `C072N9R1_DEVELOPMENT_JOIN_STOP` and no target label may be opened under this contract.

## Downstream authorization if PASS
A PASS authorizes exactly one next step: implement and execute the already-preregistered N9 multi-line `P(T)` development comparison on the frozen development identities only.

The scientific comparison is unchanged from N9:
- target `T=min(FTHG+FTAG,7)`;
- baseline numeric market feature: de-vig closing O/U2.5 logit only;
- candidate numeric market features: de-vig closing O/U0.5/1.5/2.5/3.5/4.5 logits;
- shared control: `sourceCode` one-hot only;
- paired all-five-line-valid rows only;
- median imputer + StandardScaler + OneHotEncoder + multinomial LogisticRegression `C=0.1`, `max_iter=3000`, no class weights, no search;
- five expanding chronological OOS folds 2019/20–2023/24;
- primary/secondary metrics and PASS gates exactly as frozen in N9.

Because development labels and the same broad scientific area have already been viewed elsewhere in the shared research program, any development result must be classified `REPLICATION / REPRODUCTION`, not independent confirmation, fresh evidence, blind test, or pristine confirmation.

## Hard boundaries
- No manual Draw/0-0/1-1/T=2 adjustment.
- No post-view threshold/model/feature/C/window/league subset changes.
- No 2024/25 target access in N9R1 or development.
- C070-F Confirmation1597 sealed.
- protected assets sealed.
- C073-C077 quarantined.
- formal_weight=0; no CURRENT change.
