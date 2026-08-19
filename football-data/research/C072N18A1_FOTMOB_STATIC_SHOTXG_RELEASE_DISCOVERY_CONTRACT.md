# C072-N18A1 — FotMob static shot-xG release discovery contract

## Status
- project: `football3`
- parent route: C072-N18 @ `71b345c78d3dda934918a86cbe090e27a52a2528`
- phase: **ZERO-LABEL RELEASE-METADATA DISCOVERY ONLY**
- source repository: `JaseZiv/worldfootballR_data`
- release tag: `fotmob_match_details`
- upstream release commit shown by GitHub: `e262fd4`
- target eventual history-source size: 5,000 matches, but **this discovery step does not download match-detail assets**.

## Why this source candidate
Direct SofaScore acquisition from GitHub-hosted runners ended `TECHNICAL_ACCESS_FAILURE_PRE_IDENTITY`: 0 retained identities, 0 labels, 0 model fits. That permits a new source candidate without consuming a football3 evidence pool.

`worldfootballR_data` exposes pre-collected FotMob match-detail files as immutable GitHub Release assets. Public examples show shot-level fields including match identity, x/y coordinates, expected goals, shot situation/body type and timing.

## Global-consumption classification before discovery
Shared-repository search finds historical **FotMob match-context** acquisition code, but no hit for `fotmob expectedGoals shotmap` and no stored FotMob shot-xG pipeline. Shared Airtable maintenance search for `FotMob` returns no research record.

Therefore the candidate is classified:
- provider family previously known: **YES**;
- this static release/revision already used in football3/other football project: **NOT FOUND**;
- shot-level `expectedGoals`/shotmap scientific field axis already used from FotMob: **NOT FOUND**;
- evidence class if later acquired: **NEW FIELD AXIS / STATIC MIRROR**, not “new provider”.

This classification must be revised to REPLICATION if exact-release or exact-match overlap is subsequently discovered.

## Frozen discovery operation
Read exactly one GitHub Release metadata object for:
`https://api.github.com/repos/JaseZiv/worldfootballR_data/releases/tags/fotmob_match_details`

Persist only:
- release id/tag/name/published timestamp;
- release target commitish;
- asset id;
- asset name;
- asset size;
- asset download URL;
- asset content type / updated timestamp when present.

Do **not** download any match-detail CSV in this step.

## Discovery PASS gate
PASS only if:
1. release metadata resolves successfully;
2. at least 5 `*_match_details.csv` assets exist;
3. asset `47_match_details.csv` exists (known Premier League reference);
4. total declared asset bytes > 1 MB;
5. no match/result/shot data bytes are downloaded.

Otherwise STOP_SOURCE_DISCOVERY.

## Next action after PASS
Only after exact asset names are known:
1. choose a fixed subset of assets using league identity only, not file performance;
2. freeze exact asset URLs/IDs and deterministic 5,000-match selection rule;
3. then perform one acquisition that downloads the frozen files and persists only historical chance-state fields.

No target cohort, market-anchor join, model fit, target score, C070-F access, reserve access, or C073-C077 scientific use is authorized here.
