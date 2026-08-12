# R44L2 Zero-label Engineering Correction Z2

## Status of first zero-label run

- study_id: `r44l2_lineup_role_opportunity_300`
- first workflow run: `31587211934`
- first executed HEAD: `a16a526439e787511a61d90924c24e3f5923cd23`
- first-run classification: `ENGINEERING_PRECHECK_FAIL_ALIAS_DICTIONARY_INCOMPLETE`
- first-run scientific coverage conclusion: **NOT AUTHORIZED**
- labels/model stage: **NOT OPENED**
- formal_weight: `0`

The first run selected the preregistered 300 FPL fixtures but matched only 270/300 Transfermarkt games and therefore produced 540/600 team-match rows. Among those 540 matched team-match rows, there were zero bad lineup/formation rows and zero conflicting starter duplicates.

## Zero-label diagnosis

Inspection of the unmatched identity rows showed that all 30 missing fixtures shared the same deterministic FPL team-name token: `Spurs`.

The alias dictionary already mapped `tottenham`, `tottenham hotspur`, and `tottenham hotspur fc` to canonical `tottenham`, but omitted the FPL display-name alias `spurs`.

This is a pure identity-normalization omission discovered before any target-label/model stage. It is not evidence that 30 fixtures lack lineup, formation, or position data.

## Sole authorized correction

Add exactly one deterministic alias:

`spurs -> tottenham`

No other alias, sample rule, source, join key, coverage threshold, position requirement, formation requirement, duplicate rule, or data-quality rule may change in Z2.

## Gate remains unchanged

Z2 must still require all of the original hard conditions, including:

- 300/300 unique selected fixtures;
- 300/300 Transfermarkt identity matches;
- 600/600 team-match rows;
- exactly 11 unique starters for every team-match;
- every starter has a non-empty match-specific position;
- every team-match has a non-empty formation;
- no conflicting duplicate starter identities;
- no target label, xG/xA, betting price, or final-result field used for the gate.

There is no reduced-sample fallback.

If Z2 fails any unchanged gate, terminal status remains:

`STOP_R44L2_LINEUP_ROLE_COVERAGE_INCOMPLETE`

If Z2 passes, that authorizes only creation of a separate pre-label model execution freeze. It does not itself authorize promotion or formal use.

Historical lineup `available_at` remains unverified; `pit_eligible=false` and `formal_weight=0` remain unchanged.
