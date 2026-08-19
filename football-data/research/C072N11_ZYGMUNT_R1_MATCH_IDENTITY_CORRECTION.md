# C072-N11 zygmunt R1 — zero-label match identity correction

Project: football3 only
Parent result: original zygmunt audit `ZYGMUNT_OU_SOURCE_LIMITED`.
This R1 is frozen after a source-schema/identity defect was observed but before any target/outcome field access.

## Correction scope
No scientific feature/model/threshold is changed. R1 only:
1. narrows eligible markets to exact full-match names matching `^Over/Under N.5 Goals$`;
2. excludes First Half Goals, Corners, Cards, Match Odds + O/U combinations, Exact Total Goals and other non-target families;
3. reconstructs match identity from normalized allowed metadata rather than source `EVENT_ID`.

## Frozen match key
For each eligible row:
- normalize `FULL_DESCRIPTION` by casefolding and collapsing whitespace;
- normalize the exact `EVENT` market name similarly;
- if the exact market name appears as a terminal component of FULL_DESCRIPTION, remove that component mechanically;
- combine the remaining normalized full description with exact `SCHEDULED_OFF`.

No team aliasing, fuzzy matching, date shift, manual corrections or target-informed matching is allowed.

## R1 reports
- exact O/U line markets and unique reconstructed matches;
- identity collision diagnostics;
- per-line match counts;
- T-24h/T-6h/T-1h strict and latest-completed-level proxy coverage;
- reconstructed matches with >=2, >=3 and all 5 preferred lines at each cutoff;
- matches with >=2 preferred lines at both T-6h and T-1h;
- matches with >=3 preferred lines at both T-6h and T-1h;
- all target/settlement materialization counters remain zero.

## Ruling
R1 may correct the engineering identity diagnosis only. It cannot retroactively change the original source gate to PASS. Its terminal is one of:
- `ZYGMUNT_R1_MULTILINE_STRUCTURE_PASS` if >=100 reconstructed matches have >=2 preferred lines with PIT-safe latest-completed-level observations at both T-6h and T-1h;
- `ZYGMUNT_R1_MULTILINE_STRUCTURE_LIMITED` if genuine cross-line matches exist but the gate is below 100;
- `ZYGMUNT_R1_MULTILINE_STRUCTURE_STOP` if cross-line identity cannot be established.

Even an R1 structure PASS authorizes no label access. A separate preregistered scientific contract is mandatory.
