# Market6 total-parity diagnostic R7

Research-only retrospective component diagnostic stacked on PR #208.

## Question

Does the fixed six-dimensional current-match retrospective market block contain useful information about **exact 90-minute total-goal parity** (even vs odd) beyond the existing historical core47 state?

This question is upstream of the remaining Draw-ranking bottleneck: every draw necessarily has an even total, while R6 showed that improving conditional `P(GD=0 | T even, X)` alone improved proper scores but barely activated natural Top-1 Draws.

## Frozen target

`even_total = 1` iff exact 90-minute `total_goals % 2 == 0`.

## Frozen families

All use the same binary logistic pipeline and fixed `C=0.1`.

- `core47_binary_full`
- `core47_binary_marketmatched`
- `market6_binary`
- `core47_market6_binary`

Primary comparison is **market6_binary vs core47_binary_marketmatched** on identical market-complete test rows. Combined53 is secondary and cannot replace the primary after results are visible.

## Rolling evaluation

Existing audited 26,873-row ledger; rolling test positions 2, 3 and 4 only. Market-complete selection uses market availability, never outcomes.

## Frozen development gate

- pooled market-complete test rows >= 7,000;
- each fold test rows >= 2,000;
- each fold fit rows >= 4,000;
- market6 LogLoss gain vs market-matched core47 >= 0.002;
- competition-season cluster-bootstrap p95 < 0;
- market6 AUC gain >= 0.01;
- Brier non-worse;
- market6 LogLoss wins >= 2/3 folds.

A development-gate pass is **descriptive post-view evidence only**, not scientific confirmation and not promotion authority.

## Hard boundary

The processed market references have no original quote timestamps and are therefore retrospective references, not strict PIT inputs. No Direct-T/HDA/score mutation, no forced Draw, no threshold search, no Provider request, no new collection, no B05-B07 label opening, no formal weight, and no main/CURRENT mutation are permitted.
