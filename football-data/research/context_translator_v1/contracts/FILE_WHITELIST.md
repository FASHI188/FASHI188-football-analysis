# FILE_WHITELIST

Status: FROZEN_WHITELIST / RESEARCH_ONLY
Base exact HEAD: 9a03c3aaab5d1f095d53eabd64fd03018850ed13
Branch: football3/context-translator-v1

Exactly these 23 paths are authorized for Translator V1. Any required path outside this list is a STOP condition and requires new authorization; it must not be added implicitly.

## Frozen contracts/schema (9)
1. football-data/research/context_translator_v1/contracts/TRANSLATOR_MASTER_SCOPE.md
2. football-data/research/context_translator_v1/contracts/DATA_SOURCE_PIT_CONTRACT.md
3. football-data/research/context_translator_v1/contracts/PLAYER_STRENGTH_CONTRACT.md
4. football-data/research/context_translator_v1/contracts/LINEUP_BENCH_CONTRACT.md
5. football-data/research/context_translator_v1/contracts/COACH_TACTICAL_CONTRACT.md
6. football-data/research/context_translator_v1/contracts/MATCH_CONTEXT_PROCESS_CONTRACT.md
7. football-data/research/context_translator_v1/contracts/TRANSLATED_CONTEXT_SCHEMA.json
8. football-data/research/context_translator_v1/contracts/VALIDATION_ABLATION_PREREG.md
9. football-data/research/context_translator_v1/contracts/FILE_WHITELIST.md

## Implementation/test modules (13)
10. football-data/research/context_translator_v1/source_ingest.py
11. football-data/research/context_translator_v1/identity_registry.py
12. football-data/research/context_translator_v1/pit_feature_store.py
13. football-data/research/context_translator_v1/translator_schema.py
14. football-data/research/context_translator_v1/player_strength.py
15. football-data/research/context_translator_v1/lineup_scenarios.py
16. football-data/research/context_translator_v1/coach_tactical_regime.py
17. football-data/research/context_translator_v1/match_context.py
18. football-data/research/context_translator_v1/process_hazard.py
19. football-data/research/context_translator_v1/football_context_translator.py
20. football-data/research/context_translator_v1/v2_translator_integration.py
21. football-data/research/context_translator_v1/translator_adversarial_probe.py
22. football-data/research/context_translator_v1/test_translator.py

## Independent workflow (1)
23. .github/workflows/football3-context-translator-v1.yml

## Forbidden modifications
Everything outside the 23 paths is immutable for this V1 branch, including V2 protected/core bytes inherited from the base HEAD, main, CURRENT, Airtable-related files, PR #334/R5 governance surfaces and existing workflows. No merge, Ready, force or production enablement.

## Commit discipline
The nine contract/schema/whitelist files are frozen before implementation. Subsequent changes to those frozen nine are forbidden except an explicit correction of a demonstrable contract typo that does not alter scope/thresholds and is separately reported; otherwise Translator V2/V3 is required.

The 14 non-contract paths may be created/edited only to implement or repair the already frozen V1 scope. No N+1 file.