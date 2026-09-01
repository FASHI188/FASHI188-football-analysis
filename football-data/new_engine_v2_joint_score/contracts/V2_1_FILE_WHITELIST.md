# Football3 V2.1 File Whitelist

Status: FROZEN_BEFORE_IMPLEMENTATION
Base exact HEAD: 9a03c3aaab5d1f095d53eabd64fd03018850ed13

Only the following paths may differ from the base exact HEAD on football3/new-engine-v2-1-base-repair-v1:

1. football-data/new_engine_v2_joint_score/contracts/V2_1_BASE_DESIGN_CONTRACT.md
2. football-data/new_engine_v2_joint_score/contracts/V2_1_STATE_SIGN_CONTRACT.md
3. football-data/new_engine_v2_joint_score/contracts/V2_1_PIT_UPDATE_CONTRACT.md
4. football-data/new_engine_v2_joint_score/contracts/V2_1_FILE_WHITELIST.md
5. football-data/new_engine_v2_joint_score/contracts/V2_1_VALIDATION_PREREGISTRATION.md
6. football-data/new_engine_v2_joint_score/v2_1_base.py
7. football-data/new_engine_v2_joint_score/test_v2_1_base.py
8. football-data/research/v2_pit_history/v2_1_base_validate.py
9. .github/workflows/football3-v2-1-base-repair-v1.yml

Everything else is immutable for this branch relative to the base exact HEAD. In particular: existing engine.py and all frozen V1/old-V2 code, Translator code, Oracle code/branch, venue-repair files, main, CURRENT, Airtable state, PR #334, R5 and formal-weight/global-registry assets are out of scope. No merge, Ready, force push or formal enablement.