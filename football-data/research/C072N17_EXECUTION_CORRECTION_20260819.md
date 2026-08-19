# C072-N17 execution correction — 2026-08-19

Authoritative failed execution before this correction:

- workflow run: `32265308160`
- job: `96108223180`
- failed-run artifact: `9369987405`
- artifact digest: `sha256:8011358309ef53c821bb66a8deb71e6b7ee3874ead8f7e6f5d0339638dd3ff37`

The frozen N17 contract guard and immutable N16R1 exact-2000 input hash guard both passed. The frozen N17 target join/model/OOS computation proceeded through all five folds. The process then failed only while serializing the final `summary` dictionary because NumPy scalar booleans in the already-computed scientific gate dictionary are not accepted by the standard JSON encoder.

This failure occurred after N17 development labels had been read. Therefore those 1,734 development labels are consumed and no scientific repair is permitted.

This correction changes **only JSON serialization**: a wrapper converts `numpy.generic` scalar objects to their native Python `.item()` values when `json.dumps` is called. It does not change the input identities, target join, decoded target fields, reserve boundary, features, model family, C, imputation, scaling, folds, metrics, bootstrap seed/repetitions, PASS gates or stopping rule.

No failed-run prediction metrics or target-label-derived scientific results were inspected to choose this correction. The next run is an execution reproduction of the already-frozen N17 experiment, not new/fresh evidence.

C073-C077 scientific conclusions remain quarantined. C070-F Confirmation1597 and both A-League 2025/26 reserves remain sealed.