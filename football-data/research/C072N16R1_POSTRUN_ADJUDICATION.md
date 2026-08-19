# C072-N16R1 — Post-run adjudication

## Binding terminal
`C072N16R1_FOOTIQO_NEW2000_ZERO_LABEL_DOWNLOAD_PASS`

Project: football3 only.
Classification: ZERO-LABEL DOWNLOAD / INVENTORY only. No target/result access and no model fit/score.

Parent N16 remains permanently `C072N16_FOOTIQO_NEW2000_ZERO_LABEL_DOWNLOAD_STOP`; N16R1 corrected only historical-table disambiguation before any N16 source-data POST had occurred.

## Authoritative execution evidence
- branch: `football3/c072n16r1-new2000-footiqo-protocol-correction-20260819`
- authoritative HEAD: `d488ab7e32c19e7164bad083d0b8104405479067`
- PR: `#305`
- workflow run: `32262276587`
- job: `96098121342`
- artifact: `football3-c072n16r1-footiqo-new2000-zero-label`
- artifact id: `9368768296`
- artifact ZIP SHA256: `7636b32f4b5419446fd01157c4f92eab4bd0351c458d1b58a43a2ff7f57639e6`

## Full zero-label inventory
Four fixed Footiqo historical Odds tables were retrieved without pagination drift:
- Brazil Serie A: 4,179 rows, table id 780, 9 AJAX POSTs
- Greece Super League: 1,673 rows, table id 1226, 4 AJAX POSTs
- USA MLS: 4,751 rows, table id 740, 10 AJAX POSTs
- Turkey Super Lig: 3,647 rows, table id 680, 8 AJAX POSTs

Pooled raw rows = **14,250**.
Pooled unique non-conflicting identities = **14,250**.
Conflicting identities = 0.
Exact duplicate rows removed = 0.
Total table-data POST requests =31, below frozen max80.

Full inventory CSV SHA256:
`7a7c268988e6fe23b3d85a11f67367a4cfd79c75f7da5a75aa92253fb77a3e28`

## Frozen exact-2000 asset
Selection was executed exactly as preregistered before retrieval:
`identity = sourceCode|id|matchDate|Country|League|Season|homeTeam|awayTeam`
then SHA256(identity), sort ascending by `(identity_sha256,sourceCode,id)`, retain first exactly 2,000 rows.

Selected source counts:
- Brazil Serie A: **582**
- Greece Super League: **239**
- USA MLS: **681**
- Turkey Super Lig: **498**
- total: **2,000**

Selected CSV SHA256:
`b5c988c77f7f0855481297eb5878e52742a94145bc35499f29c8ac893a596997`

Selected ordered identity SHA256:
`65491bb169bc1257ac802970a9e235324b55085863ba53fdf6c84a74b275a559`

## Selected zero-label market coverage
- core match identity completeness: 2,000/2,000 = **100%**
- H/D/A: **100%**
- BTTS Yes/No: **100%**
- O/U2.5: 2,000/2,000 = **100%**
- joint O/U1.5 +2.5 +3.5: **100%**
- all five O/U0.5/1.5/2.5/3.5/4.5: 1,888/2,000 = **94.4%**
- valid O/U0.5 pairs: 1,893
- valid O/U1.5 pairs: 2,000
- valid O/U2.5 pairs: 2,000
- valid O/U3.5 pairs: 2,000
- valid O/U4.5 pairs: 1,995

## Boundary / consumption result
Before acquisition, GitHub repository and shared Airtable maintenance-history searches found no prior records for the exact Footiqo domain/source combinations:
- Turkey Super Lig Footiqo
- Greece Super League Footiqo
- Brazil Serie A Footiqo
- USA MLS Footiqo

This supports zero-label acquisition under the current audit only. It is not by itself a pristine/fresh confirmation claim. Before any result-label join, the exact 2,000 selected identities/seasons/scientific hypothesis must be checked again against global consumption records.

N16R1 boundaries:
- score/result/target columns requested/materialized =0
- result/target values materialized =0
- model_fit=0
- model_score=0
- runtime nonce persisted/logged=0
- C073-C077 scientific conclusions used=false
- C070-F Confirmation1597 opened=false
- men's A-League 2025/26 target opened=false
- women's A-League 2025/26 target opened=false
- formal_weight=0

## Authorization boundary
This PASS establishes only an immutable zero-label 2,000-match football3 asset plus a 14,250-row full zero-label inventory. It does **not** authorize joining outcome labels or running a scientific model.

A downstream experiment must freeze its scientific hypothesis, exact target source/join, development/confirmation split, global-consumption classification and proper-score gates before any target value is read.
