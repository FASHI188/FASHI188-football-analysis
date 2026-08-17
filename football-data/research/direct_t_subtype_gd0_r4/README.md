# Direct-T Subtype → GD=0 R4

## Why this experiment exists

PR #197 established a sharp structural contrast on the fixed500: the flat model made zero natural Top-1 Draw calls, while oracle parity / oracle T made the existing conditional-GD layer produce strong Draw F1. That means the downstream draw geometry can work when upstream draw-compatible state is known.

PR #191 independently found replicated probability-quality gains from representing Draw as score subtypes (0-0, 1-1, 2-2, 3-3+) rather than one monolithic class. No repository evidence was found that this subtype representation had ever been connected directly to `P(GD=0 | T,X)`.

R4 tests exactly that missing bridge.

## Frozen structure

- exact same already-VIEWED fixed500 identities as PR #197;
- flat Direct-T is reproduced and remains unchanged;
- parent conditional-GD is reproduced;
- only conditional `GD=0` mass for `T=2,4,6` may change;
- all nonzero-GD probabilities preserve their parent relative proportions;
- T=0,1,3,5,7 are untouched;
- no forced Draw, no HDA threshold, no class-weight trick.

The fixed five-class subtype head is:

1. NON_DRAW
2. 0-0
3. 1-1
4. 2-2
5. 3-3+

It uses the existing 47 strict-prior historical state features plus four retrospective 1X2 market-geometry values (de-vig H/D/A and overround). The LightGBM hyperparameters are frozen to the PR #191 subtype settings; there is no model or hyperparameter search.

For each conditional total:

- T=2 uses P(1-1)
- T=4 uses P(2-2)
- T=6 uses P(3-3+)

The mechanism score is normalized by the unchanged flat Direct-T mass for that T. A two-parameter policy-fitted log-odds residual then adjusts only `p(GD=0)`.

## Attribution guard

R4 includes an intercept-only comparator on the identical policy rows. A positive subtype claim requires the subtype residual to beat both:

- the exact PR #197 flat baseline; and
- the intercept-only draw-mass recalibration.

So a generic upward shift in Draw probability cannot be mislabeled as evidence for the subtype mechanism.

## Development gate

A descriptive signal requires all frozen checks:

- HDA LogLoss gain vs baseline >= 0.002;
- HDA LogLoss gain vs intercept-only >= 0.001;
- paired-bootstrap LogLoss p95 < 0 against both;
- Brier and RPS non-worse vs baseline;
- Draw F1 >= 5%;
- at least 10 natural Top-1 Draw calls.

This gate is only for deciding whether a future, separately frozen OOS contract is worth designing.

## Evidence limits

The fixed500 outcomes are already viewed. Existing processed 1X2 references lack original quote timestamps. Therefore R4 is retrospective mechanism research only:

- scientific / confirmation PASS claims forbidden;
- formal_weight=0;
- B05-B07 labels remain unopened;
- no automatic OOS label opening;
- no formal model/data/config/CURRENT/main mutation.
