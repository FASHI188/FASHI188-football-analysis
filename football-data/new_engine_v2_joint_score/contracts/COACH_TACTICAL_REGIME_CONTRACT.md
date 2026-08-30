# V2 Coach and Tactical Regime Contract

Status: FROZEN BEFORE IMPLEMENTATION

## Regime state
Coach change is a regime boundary. A new regime starts with high shrinkage and uncertainty, then updates only from lawful completed matches before each cutoff. State may include formation distribution, tempo, pressing proxy, leading-state contraction tendency, trailing-state risk tendency, and substitution-timing priors when source semantics are PIT-lawful.

## Inputs
Coach identity/change date, competition/team identity, pre-cutoff historical matches, and pre-cutoff public tactical metadata with source/known_at proof. Unknown coach identity or unverifiable change timing is not guessed; tactical layer becomes unavailable for that fixture.

## Forbidden retrofill
Target-match postgame reports, actual formation inferred from events after kickoff, actual substitutions, final game state, or retrospective tactical descriptions cannot enter prematch features. They may update future regime state only after the match.

## Shrinkage
New coach/regime -> team prior -> competition prior -> global prior. Evidence accumulation must be monotone and finite. Abrupt regime shifts never inherit full prior-coach tactical parameters.

## Ablation
Retain only on stable multi-fold improvement under the frozen ablation protocol. Report before/after-change slices and uncertainty. No improvement => `REJECTED_ABLATION`; no lawful data => `BLOCKED_DATA`.