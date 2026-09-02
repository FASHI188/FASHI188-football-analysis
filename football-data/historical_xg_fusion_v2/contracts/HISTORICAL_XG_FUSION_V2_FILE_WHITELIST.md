# Football3 Historical XG Fusion V2 — file whitelist

Relative to base HEAD `08a13ce01ddf7c3408b7b89d39d44c01a3b30c9b`, only these new files may differ:

1. `.github/workflows/football3-historical-xg-fusion-v2-source-freeze.yml`
2. `.github/workflows/football3-historical-xg-fusion-v2.yml`
3. `football-data/historical_xg_fusion_v2/contracts/HISTORICAL_XG_FUSION_V2_PREREGISTRATION.md`
4. `football-data/historical_xg_fusion_v2/contracts/HISTORICAL_XG_FUSION_V2_FILE_WHITELIST.md`
5. `football-data/historical_xg_fusion_v2/source/fetch_understat_2024.py`
6. `football-data/historical_xg_fusion_v2/data/XG_FUSION_V2_DATA_IDENTITY.json`
7. `football-data/historical_xg_fusion_v2/historical_xg_fusion_v2.py`
8. `football-data/historical_xg_fusion_v2/test_historical_xg_fusion_v2.py`

All pre-existing files from the parent Historical XG V1 branch are immutable in this task, including its model implementation, contracts, tests and workflow.

Forbidden modifications include main/CURRENT/Airtable/PR#334/R5, frozen V1, V2/V2.1, Translator/Oracle, formal weights, and any existing model file. No merge, Ready, force, or formal enablement.
